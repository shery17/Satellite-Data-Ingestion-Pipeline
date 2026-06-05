import logging
from prometheus_client import push_to_gateway
import config
from pipeline import run_ingestion

# Setup logging formatting handlers globally
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.FileHandler("logs/pipeline.log"),
        logging.StreamHandler()
    ]
)

if __name__ == "__main__":
    try:
        run_ingestion()
    except Exception as runtime_fatal:
        logging.critical(f"Fatal operational exception: {runtime_fatal}")
        config.METRIC_SYSTEM_STATUS.set(0)
    finally:
        logging.info(f"Worker cycle complete. Synchronizing metrics with Pushgateway at {config.PUSHGATEWAY_URL}...")
        try:
            push_to_gateway(config.PUSHGATEWAY_URL, job='satellite_pipeline_worker', registry=config.registry)
            logging.info("Telemetry network sync successful. Exiting execution script.")
        except Exception as push_err:
            logging.error(f"Failed to transmit operational telemetry to gateway: {push_err}")