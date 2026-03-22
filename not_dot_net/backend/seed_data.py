"""Seed data for development — ~100 people and ~20 workflows."""

import random

TEAMS = [
    "Plasma Physics",
    "Instrumentation",
    "Space Weather",
    "Theory & Simulation",
    "Administration",
]

OFFICES = [f"{bldg}{floor}{room:02d}" for bldg in "ABC" for floor in "123" for room in range(1, 12)]

TITLES_BY_STATUS = {
    "researcher": ["Research Scientist", "Research Director", "Senior Researcher", "Associate Researcher"],
    "engineer": ["Senior Engineer", "Research Engineer", "Software Engineer", "Electronics Engineer"],
    "phd_student": ["PhD Student"],
    "postdoc": ["Postdoc"],
    "intern": ["Intern"],
    "visitor": ["Visiting Researcher", "Visiting Professor"],
    "admin_staff": ["Administrative Assistant", "HR Officer", "IT Support", "Lab Manager", "Secretary"],
}

FIRST_NAMES = [
    "Marie", "Pierre", "Sophie", "Lucas", "Emma", "Thomas", "Camille", "Jean",
    "Alice", "Nicolas", "Léa", "Hugo", "Chloé", "Maxime", "Julie", "Antoine",
    "Clara", "Alexandre", "Inès", "Raphaël", "Manon", "Louis", "Sarah", "Gabriel",
    "Laura", "Théo", "Margaux", "Adrien", "Pauline", "Victor", "Anaïs", "Quentin",
    "Charlotte", "Julien", "Océane", "Romain", "Mathilde", "Clément", "Émilie", "Florian",
    "Aurélie", "Bastien", "Marine", "Damien", "Céline", "Thibault", "Mélanie", "Xavier",
    "Nathalie", "Yann", "Sandrine", "Olivier", "Isabelle", "François", "Véronique",
    "Éric", "Laurence", "Stéphane", "Catherine", "Frédéric", "Hélène", "Arnaud",
]

LAST_NAMES = [
    "Dumont", "Martin", "Bernard", "Petit", "Leroy", "Moreau", "Dupont", "Roux",
    "Lambert", "Bonnet", "Girard", "Morel", "Simon", "Laurent", "Michel", "Garcia",
    "Thomas", "Robert", "Richard", "Lefebvre", "David", "Mercier", "Bertrand", "Fournier",
    "Dubois", "Blanc", "Guérin", "Perrin", "Robin", "Faure", "Fontaine", "Chevalier",
    "Renard", "Picard", "Gauthier", "Barbier", "Marchand", "Lemaire", "Masson", "Collet",
    "Muller", "Deschamps", "Brun", "Leclerc", "Vidal", "Boucher", "Charpentier", "Rey",
]


def _generate_people(n: int = 100, rng: random.Random | None = None) -> list[dict]:
    """Generate n fake people with realistic LPP-style data."""
    rng = rng or random.Random(42)  # deterministic for reproducible dev data

    # Role distribution: ~60% staff, ~15% member (phd/intern), ~15% director, ~10% admin_staff
    statuses_weights = [
        ("researcher", "staff", 35),
        ("engineer", "staff", 15),
        ("phd_student", "member", 10),
        ("postdoc", "staff", 10),
        ("intern", "member", 5),
        ("visitor", "member", 5),
        ("admin_staff", "staff", 10),
        ("researcher", "director", 10),
    ]
    statuses = []
    roles = []
    for status, role, weight in statuses_weights:
        statuses.extend([status] * weight)
        roles.extend([role] * weight)

    used_emails = set()
    people = []

    for i in range(n):
        first = rng.choice(FIRST_NAMES)
        last = rng.choice(LAST_NAMES)

        # Ensure unique emails
        base_email = f"{first.lower().replace('é','e').replace('è','e').replace('ë','e').replace('ê','e').replace('à','a').replace('â','a').replace('î','i').replace('ï','i').replace('ô','o').replace('û','u').replace('ù','u').replace('ç','c')}.{last.lower().replace('é','e').replace('è','e').replace('ê','e').replace('ë','e').replace('à','a').replace('â','a').replace('î','i').replace('ï','i').replace('ô','o').replace('û','u').replace('ù','u').replace('ç','c')}@lpp.polytechnique.fr"
        email = base_email
        suffix = 2
        while email in used_emails:
            email = base_email.replace("@", f"{suffix}@")
            suffix += 1
        used_emails.add(email)

        idx = rng.randint(0, len(statuses) - 1)
        status = statuses[idx]
        role = roles[idx]
        title = rng.choice(TITLES_BY_STATUS[status])

        people.append({
            "email": email,
            "full_name": f"{first} {last}",
            "team": rng.choice(TEAMS),
            "office": rng.choice(OFFICES),
            "phone": f"+33 1 69 33 {4000 + i:04d}",
            "title": title,
            "employment_status": status,
            "role": role,
        })

    return people


FAKE_USERS = _generate_people(100)


# --- Workflow seed data ---

WORKFLOW_SEEDS = [
    # VPN access requests in various states
    {"type": "vpn_access", "step": "request", "action": None, "data": {"target_name": "Carlos Rivera", "target_email": "carlos.rivera@external.org", "justification": "Needs remote access to simulation cluster"}},
    {"type": "vpn_access", "step": "approval", "action": "submit", "data": {"target_name": "Yuki Tanaka", "target_email": "yuki.tanaka@external.org", "justification": "Collaborator on MMS project"}},
    {"type": "vpn_access", "step": "done", "action": "approve", "data": {"target_name": "Anna Müller", "target_email": "anna.mueller@tu-berlin.de", "justification": "Joint plasma simulation campaign"}},
    {"type": "vpn_access", "step": "rejected", "action": "reject", "data": {"target_name": "Bob Smith", "target_email": "bob@example.com", "justification": "Temporary access"}, "comment": "Insufficient justification"},
    {"type": "vpn_access", "step": "approval", "action": "submit", "data": {"target_name": "Mei Lin", "target_email": "mei.lin@pku.edu.cn", "justification": "Data analysis for BepiColombo"}},
    {"type": "vpn_access", "step": "request", "action": None, "data": {"target_name": "Dmitry Volkov", "target_email": "d.volkov@iki.rssi.ru"}},
    # Onboarding requests in various states
    {"type": "onboarding", "step": "request", "action": None, "data": {"person_name": "Lena Fischer", "person_email": "lena.fischer@tu-berlin.de", "role_status": "postdoc", "team": "Space Weather", "start_date": "2026-04-15"}},
    {"type": "onboarding", "step": "newcomer_info", "action": "submit", "data": {"person_name": "James Chen", "person_email": "james.chen@caltech.edu", "role_status": "researcher", "team": "Plasma Physics", "start_date": "2026-04-01"}},
    {"type": "onboarding", "step": "admin_validation", "action": "submit", "data": {"person_name": "Aisha Patel", "person_email": "aisha.patel@imperial.ac.uk", "role_status": "phd_student", "team": "Theory & Simulation", "start_date": "2026-05-01", "phone": "+44 20 7589 5111", "emergency_contact": "Parent: +44 7700 900000"}},
    {"type": "onboarding", "step": "done", "action": "approve", "data": {"person_name": "Marco Rossi", "person_email": "marco.rossi@inaf.it", "role_status": "visitor", "team": "Instrumentation", "start_date": "2026-03-01", "phone": "+39 06 4993 4560", "emergency_contact": "Partner: +39 331 234 5678"}},
    {"type": "onboarding", "step": "rejected", "action": "reject", "data": {"person_name": "Test Person", "person_email": "test@test.com", "role_status": "intern", "team": "Administration", "start_date": "2026-06-01"}, "comment": "Position cancelled"},
    {"type": "onboarding", "step": "newcomer_info", "action": "submit", "data": {"person_name": "Sakura Yamamoto", "person_email": "sakura.yamamoto@isas.jaxa.jp", "role_status": "researcher", "team": "Space Weather", "start_date": "2026-04-10"}},
    {"type": "onboarding", "step": "request", "action": None, "data": {"person_name": "Henrik Svensson", "person_email": "henrik@kth.se", "role_status": "phd_student", "team": "Plasma Physics", "start_date": "2026-09-01"}},
    {"type": "onboarding", "step": "admin_validation", "action": "submit", "data": {"person_name": "Fatima Al-Said", "person_email": "fatima.alsaid@kaust.edu.sa", "role_status": "postdoc", "team": "Theory & Simulation", "start_date": "2026-05-15", "phone": "+966 12 808 0000", "emergency_contact": "Sibling: +966 50 123 4567"}},
    # More VPN in different states
    {"type": "vpn_access", "step": "done", "action": "approve", "data": {"target_name": "Elena Popov", "target_email": "elena.popov@msu.ru", "justification": "Solar wind data analysis collaboration"}},
    {"type": "vpn_access", "step": "approval", "action": "submit", "data": {"target_name": "Pedro Santos", "target_email": "pedro.santos@esa.int", "justification": "ESA project partner — JUICE mission"}},
    {"type": "vpn_access", "step": "request", "action": None, "data": {"target_name": "Ingrid Olsen", "target_email": "ingrid.olsen@uio.no", "justification": "Aurora modeling project"}},
    {"type": "onboarding", "step": "done", "action": "approve", "data": {"person_name": "Wei Zhang", "person_email": "wei.zhang@cas.cn", "role_status": "researcher", "team": "Instrumentation", "start_date": "2026-02-15", "phone": "+86 10 6879 7000", "emergency_contact": "Spouse: +86 138 0000 1234"}},
    {"type": "onboarding", "step": "newcomer_info", "action": "submit", "data": {"person_name": "Priya Sharma", "person_email": "priya.sharma@iisc.ac.in", "role_status": "phd_student", "team": "Space Weather", "start_date": "2026-06-15"}},
    {"type": "vpn_access", "step": "rejected", "action": "reject", "data": {"target_name": "Unknown User", "target_email": "unknown@unknown.com"}, "comment": "Cannot verify identity"},
]
