import csv

# These catalogs were copied from Siril
# https://gitlab.com/free-astro/siril/-/tree/master/data
def parse_siril_catalog(name, base="catalogs/", prefer_alias = True):
    with open(base + name) as f:
        rows = []
        for row in csv.DictReader(f):
            row["ra"] = float(row["ra"])
            row["dec"] = float(row["dec"])
            match row.get("mag"):
                case "":
                    del row["mag"]
                case val if val is not None:
                    row["mag"] = float(val)
            match row.get("diameter"):
                case "":
                    del row["diameter"]
                case val if val is not None:
                    row["diameter"] = float(val)
            rows.append(row)
        return rows

def filter_catalog(data, dec_min, dec_max, ra_min, ra_max):
    return [o for o in data if dec_min <= o["dec"] < dec_max and ra_min <= o["ra"] <= ra_max]

def relevant_stars(dec_min, dec_max, ra_min, ra_max):
    return filter_catalog(stars, dec_min, dec_max, ra_min, ra_max)

def relevant_dsos(full, dec_min, dec_max, ra_min, ra_max, vicinity = 5, tol_arcsec=60.0):
    if full:
        cat = dsos_full
    else:
        cat = dsos_lite
        vicinity = 2 * vicinity
    filtered = filter_catalog(cat, dec_min - vicinity, dec_max + vicinity, ra_min - vicinity, ra_max + vicinity)
    return dedupe_by_position(filtered, tol_arcsec)

def dedupe_by_position(objects, tol_arcsec):
    keep = []
    tol = tol_arcsec / 3600.0

    for obj in objects:
        is_dupe = False
        for existing in keep:
            dec_d = abs(obj["dec"] - existing["dec"])
            ra_d = abs(ra_diff(obj["ra"], existing["ra"]))

            if ra_d < tol and dec_d < tol:
                is_dupe = True
                break

        if not is_dupe:
            keep.append(obj)

    return keep

def ra_diff(ra2, ra1):
    d = (ra2 - ra1) % 360
    if d > 180:
        d -= 360
    return d

def ra_mid(ra1, ra2):
    d = ra_diff(ra2, ra1)
    return (ra1 + d / 2) % 360

stars = parse_siril_catalog("stars.csv")
# named stars only
stars = [
    {**o, "name": o["alias"].split("/")[-1], "alias": o["name"]}
    for o in stars if "alias" in o
]
constellations = parse_siril_catalog("constellationsnames.csv")
# constellations treated as stars
stars = stars + constellations

messier = parse_siril_catalog("messier.csv")
caldwell = parse_siril_catalog("caldwell.csv")
ngc = parse_siril_catalog("ngc.csv")
ldn = parse_siril_catalog("ldn.csv")
ic = parse_siril_catalog("ic.csv")
sh2 = parse_siril_catalog("sh2.csv")
dsos_lite = messier + caldwell + ldn + sh2
dsos_full = dsos_lite + ngc + ic

