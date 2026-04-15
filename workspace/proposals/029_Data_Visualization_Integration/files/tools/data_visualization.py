import matplotlib.pyplot as plt
import plotly.express as px

class DataVisualizer:
    def __init__(self, data):
        self.data = data

    def plot_bar(self, x, y):
        plt.bar(x, y)
        plt.show()

    def plot_line(self, x, y):
        plt.plot(x, y)
        plt.show()

    def plot_scatter(self, x, y):
        px.scatter(self.data, x=x, y=y).show()
