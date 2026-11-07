#!/usr/bin/env sh
# Download the latin woff2 subsets named in fonts.css (SIL OFL; not committed).
set -eu
cd "$(dirname "$0")"
mkdir -p fonts
for u in \
  monasans/v4/o-0IIpQmx24alC5A4PNr5TRA \
  instrumentserif/v5/jizHRFtNs2ka5fXjeivQ4LroWlx-6zAjjH7M \
  fragmentmono/v6/4iCr6K5wfMRRjxp0DA6-2CLnB4NHhg; do
  curl -fsSL -o "fonts/$(basename "$u").woff2" "https://fonts.gstatic.com/s/$u.woff2"
done
