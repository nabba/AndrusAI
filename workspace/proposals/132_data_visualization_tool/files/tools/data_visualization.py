import matplotlib.pyplot as plt
import pandas as pd

class DataVisualizer:
    def __init__(self, data):
        self.data = data

    def plot_line_chart(self, x, y, title):
        plt.figure(figsize=(10, 6))
        plt.plot(self.data[x], self.data[y])
        plt.title(title)
        plt.xlabel(x)
        plt.ylabel(y)
        plt.show()

    def plot_bar_chart(self, x, y, title):
        plt.figure(figsize=(10, 6))
        plt.bar(self.data[x], self.data[y])
        plt.title(title)
        plt.xlabel(x)
        plt.ylabel(y)
        plt.show()

# Example Usage:
# data = pd.read_csv('data.csv')
# visualizer = DataVisualizer(data)
# visualizer.plot_line_chart('Year', 'Population', 'Population Growth Over Time')