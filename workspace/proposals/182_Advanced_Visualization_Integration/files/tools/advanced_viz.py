import plotly.graph_objects as go
import pandas as pd

class EcologicalVisualizer:
    def create_interactive_map(self, geo_data, value_column, location_column):
        fig = go.Figure(go.Choropleth(
            locations=geo_data[location_column],
            z=geo_data[value_column],
            locationmode='USA-states',
            colorscale='Viridis'
        ))
        fig.update_layout(
            title_text='Ecological Data Distribution',
            geo_scope='usa'
        )
        return fig

    def create_time_series(self, data, x_col, y_col, title):
        fig = go.Figure()
        fig.add_trace(go.Scatter(
            x=data[x_col],
            y=data[y_col],
            mode='lines+markers'
        ))
        fig.update_layout(title=title)
        return fig

# Example usage:
# viz = EcologicalVisualizer()
# map_fig = viz.create_interactive_map(geo_df, 'population', 'state')
# ts_fig = viz.create_time_series(temp_df, 'date', 'temperature', 'Temperature Trends')