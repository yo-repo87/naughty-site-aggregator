from . import eporner, hqporner, pornhub, porntrex, redtube, spankbang, tube8, xhamster, xnxx, xvideos, youjizz, youporn

SOURCES = {m.NAME: m for m in (
    pornhub, xvideos, spankbang, tube8, youporn, redtube, eporner, xnxx, xhamster, youjizz, porntrex, hqporner,
)}

# Video pages from these sites may be sent to a user's targets (MeTube etc.).
# Plug-in sources add theirs through a DOMAINS tuple.
SITE_DOMAINS = ["pornhub.com", "xvideos.com", "spankbang.com", "tube8.com", "youporn.com", "redtube.com",
                "eporner.com", "xnxx.com", "xhamster.com", "youjizz.com", "porntrex.com", "hqporner.com"]
