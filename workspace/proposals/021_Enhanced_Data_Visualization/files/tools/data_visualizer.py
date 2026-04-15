import matplotlib.pyplot as plt

def plot_data(data, title='Data Visualization', xlabel='X Axis', ylabel='Y Axis'):
    plt.figure(figsize=(10, 5))
    plt.plot(data)
    plt.title(title)
    plt.xlabel(xlabel)
    plt.ylabel(ylabel)
    plt.show()