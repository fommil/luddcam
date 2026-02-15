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

def relevant_dsos(dec_min, dec_max, ra_min, ra_max, vicinity = 3, tol_arcsec=60.0):
    filtered = filter_catalog(dsos, dec_min - vicinity, dec_max + vicinity, ra_min - vicinity, ra_max + vicinity)
    return dedupe_by_position(filtered, tol_arcsec)

def dedupe_by_position(objects, tol_arcsec):
    keep = []
    tol = tol_arcsec / 3600.0

    for obj in objects:
        is_dupe = False
        for existing in keep:
            dec_diff = abs(obj["dec"] - existing["dec"])
            ra_diff = abs(obj["ra"] - existing["ra"])

            if ra_diff < tol and dec_diff < tol:
                is_dupe = True
                break

        if not is_dupe:
            keep.append(obj)

    return keep

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
dsos = messier + caldwell + ngc + ldn + ic + sh2

