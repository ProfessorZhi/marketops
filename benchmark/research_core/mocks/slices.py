"""The candidate universes the E2E cases run over.

``input.corpus`` names one of these. A slice is the *discovery result set*: the
E2E stage measures what happens after discovery (extraction, qualification,
deduplication, evidence and dossier assembly). Discovery recall is measured by
the search dataset, not here, so this file does not pretend to be a search
engine.

The lists are fixed and ordered; a slice never depends on run state.
"""

SLICES: dict[str, list[str]] = {
    "marine": [
        "northwind-marine", "van-dijk-watersport", "zuiderzee-techniek",
        "harborline-foods", "atlas-field-services", "cedar-finch",
        "kestrel-robotics", "rowan-pike", "solstice-renewables",
        "hollands-groen", "maasvallei-transport", "fluweelmode",
    ],
    "machine": [
        "braunfeld-maschinenbau", "schwarzwald-praezision", "elbwerk-automation",
        "adlerwerk-werkzeuge", "wienerwald-kueche", "donau-consulting",
        "alpenblick-hotels", "lindenhof-logistik", "kranich-software",
        "nordlicht-energie", "falkenried-kliniken", "rosenheim-papier",
    ],
    "creative": [
        "quarry-lane-studios", "amberlight-media", "tideswell-publishing",
        "ironbark-analytics", "rowan-pike", "cedar-finch", "kestrel-robotics",
        "solstice-renewables", "northwind-marine", "harborline-foods",
        "vlaamse-zorg", "donau-consulting",
    ],
}
