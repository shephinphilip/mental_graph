from threading import Thread
from crontab import CronTab

cron = CronTab()

@cron.job("0 */6 * * *")
def summarize_memories():
    print("Running memory summarization...")

def start_scheduler():
    Thread(target=cron.run, daemon=True).start()