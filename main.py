import logging
from config import validate_config, DATA_DIR, LOG_FILE
from scheduler import start_scheduler


def main():
    DATA_DIR.mkdir(exist_ok=True)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler(LOG_FILE, encoding="utf-8"),
        ],
    )
    logger = logging.getLogger("medvind")

    logger.info("Validerar konfiguration...")
    validate_config()

    logger.info("Startar Medvind Schema-scraper...")
    try:
        start_scheduler()
    except KeyboardInterrupt:
        logger.info("Avslutad av användaren")


if __name__ == "__main__":
    main()
