import os
import logging
import sys
from datetime import datetime
from apscheduler.schedulers.blocking import BlockingScheduler
from prometheus_client import push_to_gateway

# Import the core execution and your Prometheus config elements
from pipeline import run_ingestion
import config

logging.basicConfig(
    level=logging.INFO, 
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.FileHandler("logs/pipeline.log"),
        logging.StreamHandler()
    ]
)

def trigger_pipeline_job():
    logging.info("⏰ Scheduler event triggered! Launching single-pass pipeline worker...")
    try:
        # Run it natively as a clean Python function execution!
        run_ingestion()
        logging.info("Pipeline execution finished successfully.")
    except Exception as e:
        logging.error(f"Pipeline execution encountered an error: {e}")
        config.METRIC_SYSTEM_STATUS.set(0)
    finally:
        # 💡 FIX: Guarantee metrics are transmitted to Grafana on EVERY scheduler loop completion
        logging.info(f"Synchronizing telemetry metrics with Pushgateway at {config.PUSHGATEWAY_URL}...")
        try:
            push_to_gateway(config.PUSHGATEWAY_URL, job='satellite_pipeline_worker_scheduled', registry=config.registry)
            logging.info("Telemetry network sync successful. Returning to schedule standby.")
        except Exception as push_err:
            logging.error(f"Failed to transmit operational telemetry to gateway: {push_err}")

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