import matplotlib.pyplot as plt
import seaborn as sns

def create_visualization(data):
    sns.lineplot(data)
    plt.show()
    return 'Visualization displayed'