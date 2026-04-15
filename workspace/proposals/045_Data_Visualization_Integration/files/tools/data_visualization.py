import matplotlib.pyplot as plt

class DataVisualizer:
    def plot_graph(self, data, title='', x_label='', y_label=''):
        plt.figure(figsize=(10, 5))
        plt.plot(data)
        plt.title(title)
        plt.xlabel(x_label)
        plt.ylabel(y_label)
        plt.show()