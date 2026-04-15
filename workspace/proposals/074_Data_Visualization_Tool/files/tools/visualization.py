import matplotlib.pyplot as plt
import seaborn as sns
from io import StringIO

def create_ecological_plot(data, plot_type='line', **kwargs):
    """
    Generate ecological data visualizations
    Supported types: line, bar, scatter, heatmap
    """
    fig, ax = plt.subplots()
    
    if plot_type == 'line':
        sns.lineplot(data=data, ax=ax, **kwargs)
    elif plot_type == 'bar':
        sns.barplot(data=data, ax=ax, **kwargs)
    elif plot_type == 'scatter':
        sns.scatterplot(data=data, ax=ax, **kwargs)
    elif plot_type == 'heatmap':
        sns.heatmap(data=data, ax=ax, **kwargs)
    
    buf = StringIO()
    fig.savefig(buf, format='svg')
    plt.close(fig)
    return buf.getvalue()