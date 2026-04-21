var roi = ee.FeatureCollection('users/your_id/your_roi_shapefile');
var s2 = ee.ImageCollection('COPERNICUS/S2_SR');
var startDate = '2023-01-01';
var endDate = '2023-02-10';
var cloudThreshold = 1;

var composite = s2.filterBounds(roi.geometry())
  .filterDate(startDate, endDate)
  .filter(ee.Filter.lt('CLOUDY_PIXEL_PERCENTAGE', cloudThreshold))
  .median()
  .clip(roi);

Map.centerObject(roi, 10);
Map.addLayer(composite, {bands: ['B4','B3','B2'], min:0, max:3000}, 'S2 Composite');