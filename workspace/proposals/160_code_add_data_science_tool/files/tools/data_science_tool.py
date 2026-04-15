import pandas as pd
import numpy as np
import matplotlib.pyplot as plt

class DataScienceTool:
    def __init__(self, data):
        self.data = pd.DataFrame(data)

    def clean_data(self):
        # Handle missing data and outliers
        self.data = self.data.dropna()
        self.data = self.data[(np.abs(self.data - self.data.mean()) <= (3*self.data.std()))]

    def analyze_data(self):
        # Perform basic statistical analysis
        summary = self.data.describe()
        return summary

    def visualize_data(self, x, y):
        # Create a scatter plot
        plt.scatter(self.data[x], self.data[y])
        plt.xlabel(x)
        plt.ylabel(y)
        plt.show()