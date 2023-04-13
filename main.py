import datetime
import json
import random
import sys
import time
from dataclasses import dataclass

import pytz

import requests
import supabase

from bs4 import BeautifulSoup
from sicklib import get_cred, logger
from twilio.rest import Client


cm_url = get_cred("CM_URL")

twilio_number = get_cred("TWILIO_NUMBER")
my_number = get_cred("MY_NUMBER")

twilio_client = Client(
    get_cred("TWILIO_ACCOUNT_SID"),
    get_cred("TWILIO_AUTH_TOKEN"),
)

sb = supabase.create_client(
    get_cred("SUPABASE_URL"),
    get_cred("SUPABASE_KEY"),
)


@dataclass
class Event:
    _id: str
    name: str
    when: str
    room: str
    url: str


def randomize_user_agent() -> str:
    user_agents = open("user_agents.txt").read().splitlines()
    return random.choice(user_agents)


def get_events() -> list[dict]:
    try:
        r = requests.get(cm_url, headers={"User-Agent": randomize_user_agent()})
    except Exception as e:
        logger.error(f"Unable to make web request, {e}")
        sys.exit(1)

    try:
        soup = BeautifulSoup(r.text, "html.parser")
        res = soup.find_all(
            "script", attrs={"id": "__NEXT_DATA__", "type": "application/json"}
        )
        data = json.loads(res.pop().text)
        events = data["props"]["pageProps"]["data"]["events"]
    except Exception as e:
        logger.error(f"Unable to parse web data, {e}")
        sys.exit(1)

    return events


def parse_events(events: list[dict]) -> list[Event]:
    new_events = list()
    event_table = sb.table("events").select("*").execute()

    for event in events:
        _id = event["_id"]

        # check if the _id already exists in the table
        exists = [i["_id"] for i in event_table.data if i["_id"] == _id]
        if len(exists):
            continue

        when = event.get("start")
        try:
            dt = datetime.datetime.fromisoformat(when)
            when = dt.astimezone(pytz.timezone("US/Central")).strftime(
                r"%m/%d/%Y %I:%M %p"
            )
        except Exception as e:
            logger.error(f"Unable to parse datetime, {e}")

        new_events.append(
            Event(
                _id=_id,
                name=event.get("title"),
                when=when,
                room=event.get("room").get("title"),
                url=event.get("url"),
            )
        )

    return new_events


def execute_events(new_events: list[Event]) -> None:
    for e in new_events:
        _ = send_sms(f"NOMOFOMO ALERT! {e.name}, {e.when}, {e.room}")
        _ = sb.table("events").insert(e.__dict__).execute()
        logger.info(f"New event processed, {e.__dict__}")


def send_sms(body: str) -> str:
    sid = str()
    try:
        message = twilio_client.messages.create(
            body=body, from_=twilio_number, to=my_number
        )
        sid = message.sid
    except Exception as e:
        logger.error(f"SMS failed to send, {e}")
    return sid


def run() -> None:
    events = get_events()
    new_events = parse_events(events)
    execute_events(new_events)
    logger.info("nomofomo completed successfully")


if __name__ == "__main__":
    run()
