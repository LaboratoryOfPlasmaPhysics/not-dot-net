import { leaflet as L } from "nicegui-leaflet";

function loadStylesheet(href) {
  if (document.querySelector(`link[href="${href}"]`)) return Promise.resolve();
  return new Promise((resolve, reject) => {
    const link = document.createElement("link");
    link.rel = "stylesheet";
    link.href = href;
    link.onload = resolve;
    link.onerror = reject;
    document.head.appendChild(link);
  });
}

function loadScript(src) {
  if (document.querySelector(`script[src="${src}"]`)) return Promise.resolve();
  return new Promise((resolve, reject) => {
    const script = document.createElement("script");
    script.src = src;
    script.onload = resolve;
    script.onerror = reject;
    document.head.appendChild(script);
  });
}

const ALL_KINDS = ["room", "desk", "wall_plug", "asset", "other"];

export default {
  template: "<div></div>",
  props: {
    imageUrl: String,
    widthPx: Number,
    heightPx: Number,
    points: Array,
    resourcePath: String,
    mode: { type: String, default: "off" },
    visibleKinds: { type: Array, default: () => [...ALL_KINDS] },
    editingPointId: { type: String, default: null },
  },
  async mounted() {
    await this.$nextTick();
    await loadStylesheet(window.path_prefix + `${this.resourcePath}/leaflet/leaflet.css`);

    // Leaflet.draw's bundled file is a plain script expecting a global `L`
    // (it does `L.Draw = {...}` directly), but NiceGUI's own Leaflet build is
    // an ES module — `L` here is a local import, never attached to `window`.
    // Bridge it explicitly before loading the plugin script.
    window.L = L;
    await loadScript(window.path_prefix + `${this.resourcePath}/leaflet-draw/leaflet.draw.js`);
    await loadStylesheet(window.path_prefix + `${this.resourcePath}/leaflet-draw/leaflet.draw.css`);

    const bounds = [
      [0, 0],
      [this.heightPx, this.widthPx],
    ];

    this.map = L.map(this.$el, {
      crs: L.CRS.Simple,
      minZoom: -5,
      maxZoom: 4,
      zoomSnap: 0.1,
      attributionControl: false,
    });

    L.imageOverlay(this.imageUrl, bounds).addTo(this.map);
    this.map.fitBounds(bounds);

    this.kindGroups = {};
    ALL_KINDS.forEach((kind) => {
      this.kindGroups[kind] = L.layerGroup();
    });
    this.applyVisibleKinds(this.visibleKinds);

    this.shapesById = {};
    this.redrawLayers(this.points);

    this.drawHandler = new L.Draw.Polygon(this.map, { showArea: false, allowIntersection: false });
    this.editHandler = null;

    this.map.on(L.Draw.Event.CREATED, (e) => {
      if (e.layerType !== "polygon") return;
      const vertices = e.layer
        .getLatLngs()[0]
        .map((ll) => [Math.round(ll.lng), Math.round(this.heightPx - ll.lat)]);
      this.$emit("zone-drawn", { vertices });
    });

    this.map.on("click", (e) => {
      if (this.mode !== "place") return;
      this.$emit("image-click", {
        x: e.latlng.lng,
        y: this.heightPx - e.latlng.lat,
      });
    });

    this.observer = new IntersectionObserver(([entry]) => {
      if (entry.isIntersecting) this.map.invalidateSize();
    });
    this.observer.observe(this.$el);
  },
  unmounted() {
    this.editHandler?.disable();
    this.drawHandler?.disable();
    this.observer?.disconnect();
    this.map?.remove();
  },
  watch: {
    points: {
      deep: true,
      handler(newPoints) {
        this.redrawLayers(newPoints);
      },
    },
    mode(newMode) {
      this.onModeChange(newMode);
    },
    visibleKinds: {
      deep: true,
      handler(kinds) {
        this.applyVisibleKinds(kinds);
      },
    },
    editingPointId(newId) {
      if (this.editHandler) {
        this.editHandler.disable();
        this.editHandler = null;
      }
      if (!newId) return;
      const layer = this.shapesById[newId];
      if (!layer) return;
      const group = new L.FeatureGroup([layer]);
      this.editHandler = new L.EditToolbar.Edit(this.map, { featureGroup: group });
      this.editHandler.enable();
    },
  },
  methods: {
    applyVisibleKinds(kinds) {
      const visible = new Set(kinds || []);
      Object.entries(this.kindGroups).forEach(([kind, group]) => {
        const shouldShow = visible.has(kind);
        const isShown = this.map.hasLayer(group);
        if (shouldShow && !isShown) group.addTo(this.map);
        if (!shouldShow && isShown) this.map.removeLayer(group);
      });
    },
    onModeChange(newMode) {
      if (newMode === "draw") {
        this.drawHandler.enable();
      } else {
        this.drawHandler.disable();
      }
      this.redrawLayers(this.points);
    },
    redrawLayers(points) {
      Object.values(this.kindGroups).forEach((group) => group.clearLayers());
      this.shapesById = {};
      const interactive = this.mode === "off";

      (points || []).forEach((point) => {
        const isZone = Array.isArray(point.polygon) && point.polygon.length >= 3;
        let layer;
        if (isZone) {
          const latlngs = point.polygon.map(([x, y]) => [this.heightPx - y, x]);
          layer = L.polygon(latlngs, {
            color: point.highlighted ? "black" : point.color || "#1976d2",
            weight: point.highlighted ? 3 : 1,
            fillColor: point.color || "#1976d2",
            fillOpacity: 0.35,
            interactive,
          });
        } else {
          layer = L.circleMarker([this.heightPx - point.y, point.x], {
            radius: 8,
            color: point.highlighted ? "black" : "white",
            weight: point.highlighted ? 2 : 1,
            fillColor: point.color || "#1976d2",
            fillOpacity: 1,
            interactive,
          });
        }
        layer.bindTooltip(point.label, {
          permanent: true,
          direction: "right",
          className: "nicegui-leaflet-pin-label",
        });
        if (interactive) {
          layer.on("click", () => this.$emit("pin-click", { id: point.id }));
        }
        (this.kindGroups[point.kind] || this.kindGroups.other).addLayer(layer);
        this.shapesById[point.id] = layer;
      });
    },
    finishEditing() {
      if (!this.editHandler) return null;
      this.editHandler.save();
      const layer = this.shapesById[this.editingPointId];
      this.editHandler.disable();
      this.editHandler = null;
      if (!layer) return null;
      return layer.getLatLngs()[0].map((ll) => [Math.round(ll.lng), Math.round(this.heightPx - ll.lat)]);
    },
  },
};
