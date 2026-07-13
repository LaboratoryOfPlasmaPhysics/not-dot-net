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

export default {
  template: "<div></div>",
  props: {
    imageUrl: String,
    widthPx: Number,
    heightPx: Number,
    points: Array,
    resourcePath: String,
  },
  async mounted() {
    await this.$nextTick();
    await loadStylesheet(window.path_prefix + `${this.resourcePath}/leaflet/leaflet.css`);

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

    this.markers = [];
    this.redrawMarkers(this.points);

    this.map.on("click", (e) => {
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
    this.observer?.disconnect();
    this.map?.remove();
  },
  watch: {
    points: {
      deep: true,
      handler(newPoints) {
        this.redrawMarkers(newPoints);
      },
    },
  },
  methods: {
    redrawMarkers(points) {
      this.markers.forEach((marker) => this.map.removeLayer(marker));
      this.markers = (points || []).map((point) =>
        L.circleMarker([this.heightPx - point.y, point.x], {
          radius: 8,
          color: point.highlighted ? "black" : "white",
          weight: point.highlighted ? 2 : 1,
          fillColor: point.color || "#1976d2",
          fillOpacity: 1,
          interactive: false,
        })
          .bindTooltip(point.label, { permanent: true, direction: "right", className: "nicegui-leaflet-pin-label" })
          .addTo(this.map)
      );
    },
  },
};
