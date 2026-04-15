import matplotlib.pyplot as plt
import seaborn as sns

def plot_time_series(data, xlabel, ylabel, title):
    plt.figure(figsize=(10, 6))
    sns.lineplot(data=data)
    plt.xlabel(xlabel)
    plt.ylabel(ylabel)
    plt.title(title)
    plt.show()

def plot_bar_chart(data, xlabel, ylabel, title):
    plt.figure(figsize=(10, 6))
    sns.barplot(data=data)
    plt.xlabel(xlabel)
    plt.ylabel(ylabel)
    plt.title(title)
    plt.show()
