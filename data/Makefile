polygons.csv: polygons/Aksai-Chin.csv polygons/Demchok-sector.csv polygons/Gue-Kaurik-disputed-area.csv \
		polygons/Shipki-La-area.csv polygons/Nilang-Jadhang-disputed-area.csv polygons/Barahoti-disputed-area.csv \
		polygons/South-Tibet.csv polygons/Trans-Karakoram-Tract.csv polygons/Siachen-Glacier-Disputed-Area.csv \
		polygons/PAK-admin-Kashmir.csv polygons/Trans-Karakoram-Tract.csv polygons/Jammu-and-Kashmir.csv \
		polygons/Ladakh.csv polygons/Crimea.csv
	duckdb -c "copy ( select * from read_csv('polygons/*.csv') ) to 'polygons.csv' (format csv)"

polygons/Aksai-Chin.csv:
	./get-one-osm-polygon.sh 2713465 CN IN $@

polygons/Trans-Karakoram-Tract.csv:
	./get-one-osm-polygon.sh 7935380 CN IN $@

polygons/Demchok-sector.csv:
	./get-one-osm-polygon.sh 2713466 IN CN $@

polygons/Gue-Kaurik-disputed-area.csv:
	./get-one-osm-polygon.sh 2713483 IN CN $@

polygons/Shipki-La-area.csv:
	./get-one-osm-polygon.sh 2713485 IN CN $@

polygons/Nilang-Jadhang-disputed-area.csv:
	./get-one-osm-polygon.sh 2713676 IN CN $@

polygons/Barahoti-disputed-area.csv:
	./get-one-osm-polygon.sh 2713484 IN CN $@

polygons/South-Tibet.csv:
	./get-one-osm-polygon.sh 3202329 IN CN $@

polygons/Siachen-Glacier-Disputed-Area.csv:
	./get-one-osm-polygon.sh 13559521 IN PK $@

# polygons/IND-admin-Kashmir.csv:
# 	./get-one-osm-polygon.sh 13414392 IN PK $@

polygons/Jammu-and-Kashmir.csv:
	./get-one-osm-polygon.sh 1943188 IN PK $@

polygons/Ladakh.csv:
	./get-one-osm-polygon.sh 5515045 IN PK $@

polygons/PAK-admin-Kashmir.csv:
	./get-one-osm-polygon.sh 13414393 PK IN $@

polygons/Crimea.csv:
	./get-one-osm-polygon.sh 3788824 RU UA $@
