import logging
from datetime import datetime
from apscheduler.schedulers.blocking import BlockingScheduler
from prometheus_client import push_to_gateway

from pipeline import run_ingestion
import config
import metrics

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.FileHandler(f"{config.LOG_DIR}/pipeline.log"),
        logging.StreamHandler()
    ]
)


def trigger_pipeline_job():
    logging.info("Scheduler event triggered. Launching single-pass pipeline worker...")
    try:
        run_ingestion()
        logging.info("Pipeline execution finished successfully.")
    except Exception as e:
        logging.error(f"Pipeline execution encountered an error: {e}")
        metrics.SYSTEM_STATUS.set(0)
    finally:
        logging.info(f"Synchronizing telemetry metrics with Pushgateway at {config.PUSHGATEWAY_URL}...")
        try:
            push_to_gateway(
                config.PUSHGATEWAY_URL,
                job="satellite_pipeline_worker_scheduled",
                registry=metrics.registry,
            )
            logging.info("Telemetry sync successful. Returning to schedule standby.")
        except Exception as push_err:
            logging.error(f"Failed to transmit telemetry to Pushgateway: {push_err}")


if __name__ == "__main__":
    scheduler = BlockingScheduler()
    logging.info("APScheduler daemon initialised.")

    scheduler.add_job(
        trigger_pipeline_job,
        "interval",
        minutes=15,
        id="satellite_pipeline_trigger",
        next_run_time=datetime.now(),  # First run fires immediately on startup
    )

    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        logging.info("Scheduler daemon shut down cleanly.")
