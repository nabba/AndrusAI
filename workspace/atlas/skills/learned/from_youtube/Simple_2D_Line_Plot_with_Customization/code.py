import matplotlib.pyplot as plt
from matplotlib import style
%matplotlib inline

# Data
web_customers = [123, 645, 950, 1290, 1630, 1450, 1034, 1295, 465]
time_hrs = [7, 8, 9, 10, 11, 12, 13, 14, 15]

# Style and Plot
style.use('ggplot')
plt.plot(time_hrs, web_customers, color='b', linestyle='--', linewidth=2.5, alpha=0.4)

# Axis and Labels
plt.axis([6.5, 17.5, 50, 2000])
plt.xlabel('Time (hrs)')
plt.ylabel('Users')
plt.title('Website Traffic')

# Annotation
plt.annotate('Max', ha='center', va='bottom', xytext=(11, 1630), xy=(11, 1630))

plt.show()