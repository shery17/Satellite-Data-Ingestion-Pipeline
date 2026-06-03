import os
import logging
import sys
from datetime import datetime
from apscheduler.schedulers.blocking import BlockingScheduler

# Import the direct function cleanly from your pipeline file
from pipeline import run_ingestion

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')

def trigger_pipeline_job():
    logging.info("⏰ Scheduler event triggered! Launching single-pass pipeline worker...")
    try:
        # Run it natively as a clean Python function execution!
        run_ingestion()
        logging.info("Pipeline execution finished successfully. Returning to schedule standby.")
    except Exception as e:
        logging.error(f"Pipeline execution encountered an error: {e}")

if __name__ == "__main__":
    scheduler = BlockingScheduler()
    logging.info("Standalone APScheduler Daemon Initialized.")
    
    # Schedule the native function trigger
    scheduler.add_job(
        trigger_pipeline_job, 
        'interval', 
        minutes=15, 
        id='satellite_pipeline_trigger',
        next_run_time=datetime.now() # First run fires immediately
    )
    
    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        logging.info("Scheduler daemon shut down cleanly.")