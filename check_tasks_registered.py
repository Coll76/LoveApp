from celery import current_app
print(current_app.tasks.keys())