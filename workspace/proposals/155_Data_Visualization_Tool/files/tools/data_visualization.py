import matplotlib.pyplot as plt
import plotly.express as px
import pandas as pd

class DataVisualizationTool:
    def plot_line(self, data: pd.DataFrame, x: str, y: str, title: str):
        plt.figure(figsize=(10, 5))
        plt.plot(data[x], data[y])
        plt.title(title)
        plt.show()

    def plot_bar(self, data: pd.DataFrame, x: str, y: str, title: str):
        fig = px.bar(data, x=x, y=y, title=title)
        fig.show()