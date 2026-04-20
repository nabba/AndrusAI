SELECT buildings.name, zones.hazard_level 
FROM buildings, flood_zones as zones 
WHERE ST_Within(buildings.geom, zones.geom);