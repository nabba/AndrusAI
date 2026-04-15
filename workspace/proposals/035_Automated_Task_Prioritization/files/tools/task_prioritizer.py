from datetime import datetime

def prioritize_tasks(tasks):
    # Priority criteria: deadline, task type, assigned crew
    tasks.sort(key=lambda x: (x['deadline'], x['type'], x['crew']))
    return tasks