from celery import Celery
app = Celery('tasks', broker='pyamqp://guest@localhost//')

@app.task
def automate_task(task_function, schedule):
    task_function()

def schedule_task(task_function, schedule):
    automate_task.apply_async(task_function, schedule=schedule)
