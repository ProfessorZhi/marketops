"""The synthetic corpus: forty fictional companies, three countries, three languages.

Every record here is invented. Names, domains (all under the reserved ``.example``
TLD), people and addresses are fictional; any resemblance to a real company is
unintended. That is deliberate — this corpus is the *synthetic* half of the
benchmark, and it must never be mistaken for market evidence.

The same records feed four datasets, which is why they live in one place:

  * fetch        -- the pages are served over HTTP and read by real readers;
  * extraction   -- the page bytes are the snapshot an extractor must read;
  * e2e          -- the mock providers search this corpus, so a dossier can be
                    checked against the corpus rather than against the gold;
  * qualification-- the company facts are the prospect facts.

Roles are written the way a real site writes them ("Managing Director",
"Head of Partnerships"). ``decision_maker`` says whether the role has authority
over the brief's decision, which is what the extraction benchmark scores.
"""

from __future__ import annotations

from typing import Any

CORPUS_VERSION = "research-core-corpus-1.0.0"

# People names are fictional. Kept per-language so a German page does not carry
# English names, which a language detector would (correctly) flag as wrong.
COMPANIES: list[dict[str, Any]] = [
    # ------------------------------------------------------------------ EN / GB, US, IE
    {
        "slug": "northwind-marine", "name": "Northwind Marine Ltd", "domain": "northwindmarine.example",
        "country": "GB", "lang": "en", "industry": "marine equipment", "employees": 180,
        "founded": 1987, "parent": None, "aliases": ["Northwind Marine"],
        "description": "Northwind Marine designs and assembles deck fittings for commercial vessels. "
                       "It supplies shipyards across the North Sea and keeps a spare-parts warehouse in Hull.",
        "team": [
            {"name": "Marta Visser", "role": "Managing Director", "decision_maker": True},
            {"name": "Oliver Grant", "role": "Head of Partnerships", "decision_maker": True},
            {"name": "Priya Raman", "role": "Marketing Manager", "decision_maker": False},
        ],
        "emails": {"generic": ["info@northwindmarine.example", "press@northwindmarine.example"],
                   "people": {"Marta Visser": "m.visser@northwindmarine.example"}},
        "contact_method": "email",
    },
    {
        "slug": "bluepeak-logistics", "name": "Bluepeak Logistics Inc", "domain": "bluepeaklogistics.example",
        "country": "US", "lang": "en", "industry": "freight forwarding", "employees": 640,
        "founded": 2004, "parent": None, "aliases": ["Bluepeak"],
        "description": "Bluepeak Logistics moves container freight between the US Gulf Coast and Europe. "
                       "It operates three bonded warehouses and a customs brokerage.",
        "team": [
            {"name": "Daniel Reyes", "role": "Chief Executive Officer", "decision_maker": True},
            {"name": "Susan Whitfield", "role": "VP Business Development", "decision_maker": True},
        ],
        "emails": {"generic": ["hello@bluepeaklogistics.example"],
                   "people": {"Susan Whitfield": "s.whitfield@bluepeaklogistics.example"}},
        "contact_method": "email",
    },
    {
        "slug": "harborline-foods", "name": "Harborline Foods", "domain": "harborlinefoods.example",
        "country": "IE", "lang": "en", "industry": "food distribution", "employees": 95,
        "founded": 1996, "parent": None, "aliases": ["Harborline"],
        "description": "Harborline Foods distributes chilled and frozen produce to restaurants and hotels "
                       "across Leinster and Munster.",
        "team": [
            {"name": "Aoife Brennan", "role": "Founder & Managing Director", "decision_maker": True},
            {"name": "Cormac Doyle", "role": "Sales Director", "decision_maker": True},
        ],
        "emails": {"generic": ["sales@harborlinefoods.example"],
                   "people": {"Aoife Brennan": "aoife@harborlinefoods.example"}},
        "contact_method": "email",
    },
    {
        "slug": "cedar-finch", "name": "Cedar & Finch", "domain": "cedarandfinch.example",
        "country": "GB", "lang": "en", "industry": "furniture retail", "employees": 42,
        "founded": 2011, "parent": None, "aliases": ["Cedar and Finch"],
        "description": "Cedar & Finch sells solid-wood furniture from two showrooms in Bristol and Bath.",
        "team": [
            {"name": "Helen Marsh", "role": "Owner", "decision_maker": True},
        ],
        "emails": {"generic": ["shop@cedarandfinch.example"], "people": {}},
        "contact_method": "form",
    },
    {
        "slug": "atlas-field-services", "name": "Atlas Field Services LLC", "domain": "atlasfield.example",
        "country": "US", "lang": "en", "industry": "industrial maintenance", "employees": 1250,
        "founded": 1998, "parent": None, "aliases": ["Atlas Field"],
        "description": "Atlas Field Services provides scheduled maintenance crews for refineries and "
                       "chemical plants along the Gulf Coast.",
        "team": [
            {"name": "Marcus Bell", "role": "President", "decision_maker": True},
            {"name": "Elena Petrov", "role": "Director of Procurement", "decision_maker": True},
        ],
        "emails": {"generic": ["contracts@atlasfield.example"], "people": {}},
        "contact_method": "email",
    },
    {
        "slug": "meridian-health", "name": "Meridian Health Partners", "domain": "meridianhealth.example",
        "country": "US", "lang": "en", "industry": "outpatient healthcare", "employees": 310,
        "founded": 2009, "parent": None, "aliases": ["Meridian Health"],
        "description": "Meridian Health Partners runs fourteen outpatient clinics in the Midwest.",
        "team": [
            {"name": "Grace Okafor", "role": "Chief Operating Officer", "decision_maker": True},
            {"name": "Thomas Reed", "role": "Director of Marketing", "decision_maker": False},
        ],
        "emails": {"generic": ["contact@meridianhealth.example"],
                   "people": {"Grace Okafor": "g.okafor@meridianhealth.example"}},
        "contact_method": "email",
    },
    {
        "slug": "ironbark-analytics", "name": "Ironbark Analytics", "domain": "ironbarkan.example",
        "country": "GB", "lang": "en", "industry": "data analytics", "employees": 68,
        "founded": 2016, "parent": None, "aliases": ["Ironbark"],
        "description": "Ironbark Analytics builds supply-chain forecasting models for retailers.",
        "team": [
            {"name": "Nadia Karim", "role": "Co-founder & CEO", "decision_maker": True},
            {"name": "Ben Fletcher", "role": "Head of Sales", "decision_maker": True},
        ],
        "emails": {"generic": ["team@ironbarkan.example"],
                   "people": {"Ben Fletcher": "ben@ironbarkan.example"}},
        "contact_method": "email",
    },
    {
        "slug": "solstice-renewables", "name": "Solstice Renewables", "domain": "solsticerenew.example",
        "country": "IE", "lang": "en", "industry": "solar installation", "employees": 210,
        "founded": 2012, "parent": None, "aliases": ["Solstice"],
        "description": "Solstice Renewables installs rooftop solar arrays for farms and industrial sites.",
        "team": [
            {"name": "Declan Murphy", "role": "Managing Director", "decision_maker": True},
            {"name": "Siobhan Kelly", "role": "Head of Commercial", "decision_maker": True},
        ],
        "emails": {"generic": ["info@solsticerenew.example"],
                   "people": {"Siobhan Kelly": "siobhan@solsticerenew.example"}},
        "contact_method": "email",
    },
    {
        "slug": "quarry-lane-studios", "name": "Quarry Lane Studios", "domain": "quarrylane.example",
        "country": "GB", "lang": "en", "industry": "video production", "employees": 24,
        "founded": 2015, "parent": None, "aliases": ["Quarry Lane"],
        "description": "Quarry Lane Studios produces brand films and documentary series for charities and "
                       "cultural institutions.",
        "team": [
            {"name": "Imogen Price", "role": "Creative Director", "decision_maker": True},
            {"name": "Ravi Menon", "role": "Executive Producer", "decision_maker": True},
        ],
        "emails": {"generic": ["studio@quarrylane.example"],
                   "people": {"Imogen Price": "imogen@quarrylane.example"}},
        "contact_method": "email",
    },
    {
        "slug": "kestrel-robotics", "name": "Kestrel Robotics", "domain": "kestrelrobotics.example",
        "country": "US", "lang": "en", "industry": "warehouse automation", "employees": 430,
        "founded": 2013, "parent": None, "aliases": ["Kestrel"],
        "description": "Kestrel Robotics builds autonomous picking systems for e-commerce fulfilment centres.",
        "team": [
            {"name": "Victor Lin", "role": "Founder & CEO", "decision_maker": True},
            {"name": "Amara Nwosu", "role": "VP Marketing", "decision_maker": False},
        ],
        "emails": {"generic": ["info@kestrelrobotics.example"],
                   "people": {"Victor Lin": "victor@kestrelrobotics.example"}},
        "contact_method": "email",
    },
    {
        "slug": "tideswell-publishing", "name": "Tideswell Publishing", "domain": "tideswellpub.example",
        "country": "GB", "lang": "en", "industry": "academic publishing", "employees": 55,
        "founded": 1974, "parent": None, "aliases": ["Tideswell Press"],
        "description": "Tideswell Publishing produces peer-reviewed journals in the earth sciences.",
        "team": [
            {"name": "Margaret Ashby", "role": "Publisher", "decision_maker": True},
        ],
        "emails": {"generic": ["journals@tideswellpub.example"], "people": {}},
        "contact_method": "email",
    },
    {
        "slug": "vulcan-composites", "name": "Vulcan Composites Corp", "domain": "vulcancomp.example",
        "country": "US", "lang": "en", "industry": "aerospace materials", "employees": 870,
        "founded": 1989, "parent": None, "aliases": ["Vulcan Composites"],
        "description": "Vulcan Composites manufactures carbon-fibre structures for business aircraft.",
        "team": [
            {"name": "Robert Hale", "role": "Chief Executive Officer", "decision_maker": True},
            {"name": "Lena Ortiz", "role": "Director of Supply Chain", "decision_maker": True},
        ],
        "emails": {"generic": ["suppliers@vulcancomp.example"], "people": {}},
        "contact_method": "email",
    },
    {
        "slug": "amberlight-media", "name": "Amberlight Media", "domain": "amberlightmedia.example",
        "country": "IE", "lang": "en", "industry": "podcast production", "employees": 33,
        "founded": 2018, "parent": None, "aliases": ["Amberlight"],
        "description": "Amberlight Media produces narrative podcasts and audio documentaries.",
        "team": [
            {"name": "Niamh Byrne", "role": "Founder", "decision_maker": True},
            {"name": "Sean Fitzgerald", "role": "Head of Partnerships", "decision_maker": True},
        ],
        "emails": {"generic": ["hello@amberlightmedia.example"],
                   "people": {"Niamh Byrne": "niamh@amberlightmedia.example"}},
        "contact_method": "email",
    },
    {
        "slug": "rowan-pike", "name": "Rowan & Pike Architects", "domain": "rowanpike.example",
        "country": "GB", "lang": "en", "industry": "architecture", "employees": 61,
        "founded": 2001, "parent": None, "aliases": ["Rowan and Pike"],
        "description": "Rowan & Pike Architects designs civic and education buildings.",
        "team": [
            {"name": "Charlotte Rowan", "role": "Partner", "decision_maker": True},
            {"name": "James Pike", "role": "Partner", "decision_maker": True},
        ],
        "emails": {"generic": ["mail@rowanpike.example"], "people": {}},
        "contact_method": "form",
    },
    {
        "slug": "delta-ridge-mining", "name": "Delta Ridge Mining Equipment", "domain": "deltaridgemining.example",
        "country": "US", "lang": "en", "industry": "mining equipment", "employees": 1500,
        "founded": 1962, "parent": "Delta Ridge Holdings", "aliases": ["Delta Ridge Mining"],
        "description": "Delta Ridge Mining Equipment builds haul trucks and drilling rigs. It is a "
                       "subsidiary of Delta Ridge Holdings.",
        "team": [
            {"name": "Harold Finch", "role": "President", "decision_maker": True},
        ],
        "emails": {"generic": ["sales@deltaridgemining.example"], "people": {}},
        "contact_method": "email",
    },
    {
        "slug": "delta-ridge-holdings", "name": "Delta Ridge Holdings", "domain": "deltaridge.example",
        "country": "US", "lang": "en", "industry": "industrial holding company", "employees": 2100,
        "founded": 1960, "parent": None, "aliases": ["Delta Ridge"],
        "description": "Delta Ridge Holdings owns industrial equipment businesses in mining and rail.",
        "team": [
            {"name": "Patricia Vance", "role": "Chief Executive Officer", "decision_maker": True},
        ],
        "emails": {"generic": ["ir@deltaridge.example"], "people": {}},
        "contact_method": "email",
    },

    # ------------------------------------------------------------------ DE / AT
    {
        "slug": "braunfeld-maschinenbau", "name": "Braunfeld Maschinenbau GmbH", "domain": "braunfeld-mb.example",
        "country": "DE", "lang": "de", "industry": "machine tools", "employees": 320,
        "founded": 1954, "parent": None, "aliases": ["Braunfeld"],
        "description": "Braunfeld Maschinenbau baut CNC-Fräsmaschinen für den Werkzeug- und Formenbau. "
                       "Der Standort liegt in Esslingen.",
        "team": [
            {"name": "Klaus Braunfeld", "role": "Geschäftsführer", "decision_maker": True},
            {"name": "Sabine Krüger", "role": "Leiterin Vertrieb", "decision_maker": True},
            {"name": "Jonas Weber", "role": "Marketingreferent", "decision_maker": False},
        ],
        "emails": {"generic": ["info@braunfeld-mb.example"],
                   "people": {"Sabine Krüger": "s.krueger@braunfeld-mb.example"}},
        "contact_method": "email",
    },
    {
        "slug": "lindenhof-logistik", "name": "Lindenhof Logistik GmbH", "domain": "lindenhof-logistik.example",
        "country": "DE", "lang": "de", "industry": "logistics", "employees": 480,
        "founded": 1991, "parent": None, "aliases": ["Lindenhof"],
        "description": "Lindenhof Logistik betreibt Kontraktlogistik-Standorte in Nordrhein-Westfalen.",
        "team": [
            {"name": "Andreas Lindenhof", "role": "Geschäftsführer", "decision_maker": True},
            {"name": "Meike Hoffmann", "role": "Leiterin Geschäftsentwicklung", "decision_maker": True},
        ],
        "emails": {"generic": ["kontakt@lindenhof-logistik.example"],
                   "people": {"Meike Hoffmann": "m.hoffmann@lindenhof-logistik.example"}},
        "contact_method": "email",
    },
    {
        "slug": "kranich-software", "name": "Kranich Software AG", "domain": "kranich-sw.example",
        "country": "DE", "lang": "de", "industry": "ERP software", "employees": 260,
        "founded": 2002, "parent": None, "aliases": ["Kranich"],
        "description": "Kranich Software entwickelt ERP-Lösungen für den Mittelstand.",
        "team": [
            {"name": "Stefan Kranich", "role": "Vorstandsvorsitzender", "decision_maker": True},
            {"name": "Julia Brandt", "role": "Leiterin Partnerschaften", "decision_maker": True},
        ],
        "emails": {"generic": ["info@kranich-sw.example"],
                   "people": {"Julia Brandt": "j.brandt@kranich-sw.example"}},
        "contact_method": "email",
    },
    {
        "slug": "schwarzwald-praezision", "name": "Schwarzwald Präzision GmbH", "domain": "sw-praezision.example",
        "country": "DE", "lang": "de", "industry": "precision parts", "employees": 150,
        "founded": 1978, "parent": None, "aliases": ["Schwarzwald Präzision"],
        "description": "Schwarzwald Präzision fertigt Drehteile aus Edelstahl für die Medizintechnik.",
        "team": [
            {"name": "Thomas Benz", "role": "Geschäftsführer", "decision_maker": True},
        ],
        "emails": {"generic": ["anfrage@sw-praezision.example"], "people": {}},
        "contact_method": "email",
    },
    {
        "slug": "nordlicht-energie", "name": "Nordlicht Energie GmbH", "domain": "nordlicht-energie.example",
        "country": "DE", "lang": "de", "industry": "wind energy", "employees": 390,
        "founded": 2007, "parent": None, "aliases": ["Nordlicht"],
        "description": "Nordlicht Energie plant und betreibt Windparks in Schleswig-Holstein.",
        "team": [
            {"name": "Frauke Petersen", "role": "Geschäftsführerin", "decision_maker": True},
            {"name": "Hauke Thomsen", "role": "Leiter Projektakquise", "decision_maker": True},
        ],
        "emails": {"generic": ["post@nordlicht-energie.example"], "people": {}},
        "contact_method": "form",
    },
    {
        "slug": "adlerwerk-werkzeuge", "name": "Adlerwerk Werkzeuge GmbH", "domain": "adlerwerk.example",
        "country": "DE", "lang": "de", "industry": "hand tools", "employees": 88,
        "founded": 1949, "parent": None, "aliases": ["Adlerwerk"],
        "description": "Adlerwerk Werkzeuge stellt Handwerkzeuge für Schreiner und Zimmerleute her.",
        "team": [
            {"name": "Bernd Adler", "role": "Geschäftsführer", "decision_maker": True},
            {"name": "Katrin Sommer", "role": "Vertriebsleiterin", "decision_maker": True},
        ],
        "emails": {"generic": ["vertrieb@adlerwerk.example"],
                   "people": {"Katrin Sommer": "k.sommer@adlerwerk.example"}},
        "contact_method": "email",
    },
    {
        "slug": "falkenried-kliniken", "name": "Falkenried Kliniken GmbH", "domain": "falkenried-kliniken.example",
        "country": "DE", "lang": "de", "industry": "private clinics", "employees": 720,
        "founded": 1985, "parent": None, "aliases": ["Falkenried"],
        "description": "Falkenried Kliniken betreibt vier private Fachkliniken in Hamburg und Bremen.",
        "team": [
            {"name": "Dr. Ulrike Falkenried", "role": "Geschäftsführerin", "decision_maker": True},
        ],
        "emails": {"generic": ["verwaltung@falkenried-kliniken.example"], "people": {}},
        "contact_method": "email",
    },
    {
        "slug": "elbwerk-automation", "name": "Elbwerk Automation AG", "domain": "elbwerk-automation.example",
        "country": "DE", "lang": "de", "industry": "factory automation", "employees": 540,
        "founded": 1999, "parent": None, "aliases": ["Elbwerk"],
        "description": "Elbwerk Automation integriert Roboterzellen in Produktionslinien.",
        "team": [
            {"name": "Martin Elbe", "role": "Vorstand Technik", "decision_maker": True},
            {"name": "Nina Albrecht", "role": "Leiterin Marketing", "decision_maker": False},
        ],
        "emails": {"generic": ["info@elbwerk-automation.example"], "people": {}},
        "contact_method": "email",
    },
    {
        "slug": "rosenheim-papier", "name": "Rosenheim Papier GmbH", "domain": "rosenheim-papier.example",
        "country": "DE", "lang": "de", "industry": "paper packaging", "employees": 210,
        "founded": 1968, "parent": None, "aliases": ["Rosenheim Papier"],
        "description": "Rosenheim Papier produziert Faltschachteln aus Recyclingkarton.",
        "team": [
            {"name": "Georg Maier", "role": "Geschäftsführer", "decision_maker": True},
        ],
        "emails": {"generic": ["office@rosenheim-papier.example"], "people": {}},
        "contact_method": "form",
    },
    {
        "slug": "wienerwald-kueche", "name": "Wienerwald Küche GmbH", "domain": "wienerwald-kueche.example",
        "country": "AT", "lang": "de", "industry": "commercial kitchens", "employees": 130,
        "founded": 1994, "parent": None, "aliases": ["Wienerwald Küche"],
        "description": "Wienerwald Küche plant und liefert Großküchen für Hotellerie und Betriebsgastronomie.",
        "team": [
            {"name": "Lukas Gruber", "role": "Geschäftsführer", "decision_maker": True},
            {"name": "Anna Moser", "role": "Leiterin Einkauf", "decision_maker": True},
        ],
        "emails": {"generic": ["office@wienerwald-kueche.example"],
                   "people": {"Anna Moser": "a.moser@wienerwald-kueche.example"}},
        "contact_method": "email",
    },
    {
        "slug": "alpenblick-hotels", "name": "Alpenblick Hotels AG", "domain": "alpenblick-hotels.example",
        "country": "AT", "lang": "de", "industry": "hotel group", "employees": 600,
        "founded": 1971, "parent": None, "aliases": ["Alpenblick"],
        "description": "Alpenblick Hotels betreibt neun Häuser in Tirol und Salzburg.",
        "team": [
            {"name": "Stefan Hofer", "role": "Vorstand", "decision_maker": True},
        ],
        "emails": {"generic": ["zentrale@alpenblick-hotels.example"], "people": {}},
        "contact_method": "email",
    },
    {
        "slug": "donau-consulting", "name": "Donau Consulting GmbH", "domain": "donau-consulting.example",
        "country": "AT", "lang": "de", "industry": "management consulting", "employees": 75,
        "founded": 2010, "parent": None, "aliases": ["Donau Consulting"],
        "description": "Donau Consulting berät Industrieunternehmen bei Prozessoptimierung.",
        "team": [
            {"name": "Elisabeth Wagner", "role": "Managing Partner", "decision_maker": True},
        ],
        "emails": {"generic": ["office@donau-consulting.example"], "people": {}},
        "contact_method": "form",
    },

    # ------------------------------------------------------------------ NL / BE (nl)
    {
        "slug": "van-dijk-watersport", "name": "Van Dijk Watersport", "domain": "vandijkwatersport.example",
        "country": "NL", "lang": "nl", "industry": "marine equipment", "employees": 48,
        "founded": 1983, "parent": None, "aliases": ["Van Dijk"],
        "description": "Van Dijk Watersport levert bootbenodigdheden en dekbeslag aan werven in Zeeland.",
        "team": [
            {"name": "Willem van Dijk", "role": "Directeur", "decision_maker": True},
            {"name": "Sanne de Wit", "role": "Hoofd Inkoop", "decision_maker": True},
        ],
        "emails": {"generic": ["info@vandijkwatersport.example"],
                   "people": {"Sanne de Wit": "s.dewit@vandijkwatersport.example"}},
        "contact_method": "email",
    },
    {
        "slug": "hollands-groen", "name": "Hollands Groen B.V.", "domain": "hollandsgroen.example",
        "country": "NL", "lang": "nl", "industry": "horticulture supplies", "employees": 260,
        "founded": 1997, "parent": None, "aliases": ["Hollands Groen"],
        "description": "Hollands Groen levert substraten en irrigatiesystemen aan tuinders.",
        "team": [
            {"name": "Bram Jansen", "role": "Algemeen Directeur", "decision_maker": True},
        ],
        "emails": {"generic": ["verkoop@hollandsgroen.example"], "people": {}},
        "contact_method": "email",
    },
    {
        "slug": "zuiderzee-techniek", "name": "Zuiderzee Techniek B.V.", "domain": "zuiderzeetechniek.example",
        "country": "NL", "lang": "nl", "industry": "marine engineering", "employees": 175,
        "founded": 2005, "parent": None, "aliases": ["Zuiderzee Techniek"],
        "description": "Zuiderzee Techniek bouwt en onderhoudt aandrijfsystemen voor binnenvaartschepen.",
        "team": [
            {"name": "Pieter Bakker", "role": "Directeur", "decision_maker": True},
            {"name": "Lieke Smit", "role": "Manager Commercie", "decision_maker": True},
        ],
        "emails": {"generic": ["info@zuiderzeetechniek.example"],
                   "people": {"Lieke Smit": "l.smit@zuiderzeetechniek.example"}},
        "contact_method": "email",
    },
    {
        "slug": "de-vries-luchtbehandeling", "name": "De Vries Luchtbehandeling", "domain": "devrieslucht.example",
        "country": "NL", "lang": "nl", "industry": "HVAC", "employees": 92,
        "founded": 1988, "parent": None, "aliases": ["De Vries Luchtbehandeling"],
        "description": "De Vries Luchtbehandeling installeert klimaatinstallaties voor kantoren en scholen.",
        "team": [
            {"name": "Hans de Vries", "role": "Directeur", "decision_maker": True},
        ],
        "emails": {"generic": ["service@devrieslucht.example"], "people": {}},
        "contact_method": "form",
    },
    {
        "slug": "brabant-food-group", "name": "Brabant Food Group B.V.", "domain": "brabantfood.example",
        "country": "NL", "lang": "nl", "industry": "food processing", "employees": 830,
        "founded": 1967, "parent": None, "aliases": ["Brabant Food"],
        "description": "Brabant Food Group verwerkt groenten voor supermarktketens in de Benelux.",
        "team": [
            {"name": "Marieke van den Berg", "role": "CEO", "decision_maker": True},
            {"name": "Joost Visser", "role": "Directeur Inkoop", "decision_maker": True},
        ],
        "emails": {"generic": ["info@brabantfood.example"], "people": {}},
        "contact_method": "email",
    },
    {
        "slug": "amstel-data", "name": "Amstel Data B.V.", "domain": "amsteldatacentres.example",
        "country": "NL", "lang": "nl", "industry": "data centres", "employees": 145,
        "founded": 2014, "parent": None, "aliases": ["Amstel Data"],
        "description": "Amstel Data exploiteert twee datacenters in de regio Amsterdam.",
        "team": [
            {"name": "Ruben Mulder", "role": "Directeur", "decision_maker": True},
            {"name": "Femke Bos", "role": "Hoofd Sales", "decision_maker": True},
        ],
        "emails": {"generic": ["sales@amsteldatacentres.example"],
                   "people": {"Femke Bos": "f.bos@amsteldatacentres.example"}},
        "contact_method": "email",
    },
    {
        "slug": "fluweelmode", "name": "Fluweelmode B.V.", "domain": "fluweelmode.example",
        "country": "BE", "lang": "nl", "industry": "fashion wholesale", "employees": 38,
        "founded": 2013, "parent": None, "aliases": ["Fluweelmode"],
        "description": "Fluweelmode verkoopt dameskleding aan boetieks in Vlaanderen.",
        "team": [
            {"name": "Elke Peeters", "role": "Zaakvoerder", "decision_maker": True},
        ],
        "emails": {"generic": ["hallo@fluweelmode.example"], "people": {}},
        "contact_method": "form",
    },
    {
        "slug": "kempen-bouw", "name": "Kempen Bouw B.V.", "domain": "kempenbouw.example",
        "country": "BE", "lang": "nl", "industry": "construction", "employees": 410,
        "founded": 1982, "parent": None, "aliases": ["Kempen Bouw"],
        "description": "Kempen Bouw realiseert utiliteitsbouw en renovaties in de Kempen.",
        "team": [
            {"name": "Bart Claes", "role": "Zaakvoerder", "decision_maker": True},
            {"name": "Ilse Maes", "role": "Directeur Aankoop", "decision_maker": True},
        ],
        "emails": {"generic": ["info@kempenbouw.example"],
                   "people": {"Ilse Maes": "i.maes@kempenbouw.example"}},
        "contact_method": "email",
    },
    {
        "slug": "maasvallei-transport", "name": "Maasvallei Transport B.V.", "domain": "maasvalleitransport.example",
        "country": "BE", "lang": "nl", "industry": "road transport", "employees": 220,
        "founded": 1993, "parent": None, "aliases": ["Maasvallei"],
        "description": "Maasvallei Transport verzorgt distributie in België en Zuid-Nederland.",
        "team": [
            {"name": "Tom Willems", "role": "Directeur", "decision_maker": True},
        ],
        "emails": {"generic": ["planning@maasvalleitransport.example"], "people": {}},
        "contact_method": "email",
    },
    {
        "slug": "lindeboom-meubelen", "name": "Lindeboom Meubelen B.V.", "domain": "lindeboommeubelen.example",
        "country": "BE", "lang": "nl", "industry": "furniture manufacturing", "employees": 160,
        "founded": 1959, "parent": None, "aliases": ["Lindeboom"],
        "description": "Lindeboom Meubelen produceert eiken meubelen voor de Benelux-markt.",
        "team": [
            {"name": "Koen Lindeboom", "role": "Zaakvoerder", "decision_maker": True},
        ],
        "emails": {"generic": ["verkoop@lindeboommeubelen.example"], "people": {}},
        "contact_method": "email",
    },
    {
        "slug": "antwerpen-drukwerk", "name": "Antwerpen Drukwerk B.V.", "domain": "antwerpendrukwerk.example",
        "country": "BE", "lang": "nl", "industry": "printing", "employees": 54,
        "founded": 2008, "parent": None, "aliases": ["Antwerpen Drukwerk"],
        "description": "Antwerpen Drukwerk verzorgt offsetdruk en afwerking voor uitgeverijen.",
        "team": [
            {"name": "Dirk Verhoeven", "role": "Zaakvoerder", "decision_maker": True},
        ],
        "emails": {"generic": ["druk@antwerpendrukwerk.example"], "people": {}},
        "contact_method": "form",
    },
    {
        "slug": "vlaamse-zorg", "name": "Vlaamse Zorg Groep B.V.", "domain": "vlaamsezorg.example",
        "country": "BE", "lang": "nl", "industry": "elderly care", "employees": 950,
        "founded": 1976, "parent": None, "aliases": ["Vlaamse Zorg"],
        "description": "Vlaamse Zorg Groep beheert woonzorgcentra in Oost- en West-Vlaanderen.",
        "team": [
            {"name": "An Vermeulen", "role": "Directeur-generaal", "decision_maker": True},
        ],
        "emails": {"generic": ["info@vlaamsezorg.example"], "people": {}},
        "contact_method": "email",
    },
    # ---------------------------------------------------- qualification edge types
    # Three records exist to make qualification outcomes that the other forty
    # cannot express honestly: a directory that is not a supplier, a domain that
    # publishes nothing, and a company whose own pages disagree about headcount.
    {
        "slug": "maritime-suppliers-directory",
        "name": "Maritime Suppliers Directory",
        "domain": "maritimesuppliers.example",
        "country": "GB", "lang": "en", "industry": "trade directory", "employees": 6,
        "founded": 2011, "parent": None, "aliases": ["MSD"],
        "description": "A directory that lists marine equipment suppliers. The directory itself "
                       "does not design, stock or supply equipment; it sells listings.",
        "team": [],
        "emails": {"generic": ["listings@maritimesuppliers.example"], "people": {}},
        "contact_method": "email",
        "record_type": "directory",
        # Not in search_index.json: this record exists to exercise a
        # qualification verdict, not to be discovered by a search query. Keeping
        # it out leaves the search benchmark's frozen expectations untouched.
        "indexed": False,
    },
    {
        "slug": "harbourline-marine",
        "name": "Harbourline Marine Ltd",
        "domain": "harbourline.example",
        "country": "GB", "lang": "en", "industry": "marine equipment", "employees": 180,
        "founded": 1994, "parent": None, "aliases": ["Harbourline"],
        "description": "A registered domain with no published company site. Every page serves "
                       "the same placeholder text, so no company fact can be confirmed here.",
        "team": [],
        "emails": {"generic": [], "people": {}},
        "contact_method": "none",
        "placeholder": True,
        "domain_status": "placeholder",
        "indexed": False,
    },
    {
        "slug": "nordsee-maritim",
        "name": "Nordsee Maritim Ltd",
        "domain": "nordseemaritim.example",
        "country": "GB", "lang": "en", "industry": "marine equipment", "employees": 180,
        "founded": 2002, "parent": None, "aliases": ["Nordsee Maritim"],
        "description": "Nordsee Maritim supplies mooring hardware to North Sea terminals.",
        "team": [
            {"name": "Iain MacLeod", "role": "Managing Director", "decision_maker": True},
        ],
        "team_intro": "Our founding team of 25 people still answers every enquiry.",
        "emails": {"generic": ["info@nordseemaritim.example"], "people": {}},
        "contact_method": "email",
        "indexed": False,
    },
]

#: Companies whose team page is rendered by JavaScript. The reader must execute
#: it (or the mock must model that it did) to see any person at all.
JS_TEAM_SLUGS: frozenset[str] = frozenset({"northwind-marine", "kranich-software", "zuiderzee-techniek"})

#: Companies that publish a team page but no personal address. These are the
#: negative cases: an extractor that invents `first.last@domain` fails here.
#: Derived, never hand-listed — a hand-listed set drifts the moment a record
#: gains an address and would silently stop testing the thing it names.
NO_PERSONAL_EMAIL_SLUGS: frozenset[str] = frozenset(
    c["slug"] for c in COMPANIES if c.get("team") and not c["emails"]["people"]
)

#: Parent/subsidiary pairs, for the identity benchmark.
RELATIONS: list[dict[str, str]] = [
    {"parent": "delta-ridge-holdings", "child": "delta-ridge-mining", "kind": "parent_of"},
]

#: Short aliases used by the mock search index, so a query for "Delta Ridge"
#: can surface both the parent and the equipment subsidiary.
ALIAS_INDEX: dict[str, list[str]] = {
    "delta-ridge-holdings": ["Delta Ridge", "Delta Ridge Holdings"],
    "delta-ridge-mining": ["Delta Ridge Mining", "Delta Ridge Mining Equipment"],
}


def by_slug(slug: str) -> dict[str, Any]:
    for company in COMPANIES:
        if company["slug"] == slug:
            return company
    raise KeyError(slug)


def all_people() -> list[dict[str, Any]]:
    out = []
    for company in COMPANIES:
        for person in company.get("team", []):
            out.append({**person, "company": company["name"], "slug": company["slug"]})
    return out


def personal_email(company: dict[str, Any], person_name: str) -> str | None:
    return company.get("emails", {}).get("people", {}).get(person_name)
