#!/bin/bash -ex
REL_ID=$1
CONTROLLED_BY=$2
CLAIMED_BY=$3
FILENAME=$4

curl "https://api.openstreetmap.org/api/0.6/relation/${REL_ID}/full" -s \
    | ogr2ogr -limit 1 -oo CONFIG_FILE=osmconf.ini -lco GEOMETRY=AS_WKT \
        -sql "select *, '${CONTROLLED_BY}' as controlled_by, '${CLAIMED_BY}' as claimed_by from multipolygons" \
        -f CSV "${FILENAME}" /vsistdin/ multipolygons
