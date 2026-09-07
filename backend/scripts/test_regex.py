import re

descriptions = [
    "Phase 1 Discovery & Transition — Stakeholder interviews with Festival leadership",
    "Phase 2 Optimization & Systems Setup — Sponsorship tier package redesign",
    "Phase 3 Sustained Execution — Year-round social media management",
    "2.2 Phase 1 Discovery & Transition — Stakeholder interviews" # Maybe it has a menu ID?
]

for desc in descriptions:
    original = desc
    desc = re.sub(r"^Phase\s+\d+\s+[^—]+[—-]\s*", "", desc, flags=re.IGNORECASE).strip()
    print(f"Original: {original}\nResult:   {desc}\n")
