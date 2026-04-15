import requests
import json

class RealTimeDataProcessor:
    def __init__(self, api_key=None):
        self.api_key = api_key

    def fetch_noaa_data(self, endpoint, params={}):
        base_url = 'https://api.tidesandcurrents.noaa.gov/api/prod/'
        response = requests.get(f'{base_url}{endpoint}', params=params)
        return response.json()

    def fetch_usgs_data(self, site_id, parameter_code):
        url = f'https://waterservices.usgs.gov/nwis/iv/?format=json&sites={site_id}&parameterCd={parameter_code}'
        response = requests.get(url)
        return response.json()

# Example usage:
# processor = RealTimeDataProcessor()
# noaa_data = processor.fetch_noaa_data('datagetter', {'product': 'water_level', 'station': '9414290'})
# usgs_data = processor.fetch_usgs_data('01646500', '00060')