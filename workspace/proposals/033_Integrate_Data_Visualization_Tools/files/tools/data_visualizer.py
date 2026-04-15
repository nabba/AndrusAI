import matplotlib.pyplot as plt
import seaborn as sns

def plot_data(data, plot_type='line'):
    if plot_type == 'line':
        plt.plot(data)
    elif plot_type == 'bar':
        sns.barplot(x=list(data.keys()), y=list(data.values()))
    plt.show()