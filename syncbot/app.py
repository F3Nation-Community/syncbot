import json
import logging
import re
import sys

from slack_bolt import App
from slack_bolt.adapter.aws_lambda import SlackRequestHandler
from utils.constants import LOCAL_DEVELOPMENT
from utils.helpers import get_oauth_flow, get_request_type, safe_get
from utils.routing import MAIN_MAPPER

SlackRequestHandler.clear_all_log_handlers()
if logging.getLogger().hasHandlers():
    logging.getLogger().setLevel(logging.INFO)
else:
    logging.basicConfig(level=logging.INFO, stream=sys.stdout)

app = App(
    process_before_response=not LOCAL_DEVELOPMENT,
    oauth_flow=get_oauth_flow(),
)


def handler(event, context):
    slack_request_handler = SlackRequestHandler(app=app)
    return slack_request_handler.handle(event, context)


def main_response(body, logger, client, ack, context):
    ack()
    logger.info(json.dumps(body, indent=4))
    request_type, request_id = get_request_type(body)
    run_function = safe_get(safe_get(MAIN_MAPPER, request_type), request_id)
    if run_function:
        run_function(body, client, logger, context)
    else:
        logger.error(
            f"no handler for path: {safe_get(safe_get(MAIN_MAPPER, request_type), request_id) or request_type+', '+request_id}"
        )


if LOCAL_DEVELOPMENT:
    ARGS = [main_response]
    LAZY_KWARGS = {}
else:
    ARGS = []
    LAZY_KWARGS = {
        "ack": lambda ack: ack(),
        "lazy": [main_response],
    }

MATCH_ALL_PATTERN = re.compile(".*")
app.event(MATCH_ALL_PATTERN)(*ARGS, **LAZY_KWARGS)
app.action(MATCH_ALL_PATTERN)(*ARGS, **LAZY_KWARGS)
app.view(MATCH_ALL_PATTERN)(*ARGS, **LAZY_KWARGS)
app.command(MATCH_ALL_PATTERN)(*ARGS, **LAZY_KWARGS)


if __name__ == "__main__":
    app.start(3000)
