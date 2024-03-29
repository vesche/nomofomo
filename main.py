import datetime
import json
import random
import time
from dataclasses import dataclass
from dateutil.relativedelta import relativedelta

import pytz

import requests
import supabase

from bs4 import BeautifulSoup
from sicklib import get_cred, logger
from twilio.rest import Client

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
class EventCM:
    _id: str
    name: str
    when: str
    room: str
    url: str


@dataclass
class EventHB:
    name: str
    dates: str


class YikesAnError(Exception):
    pass


def randomize_user_agent() -> str:
    user_agents = open("user_agents.txt").read().splitlines()
    return random.choice(user_agents)


def get_events_cm() -> list[dict]:
    cm_url = get_cred("CM_URL")

    try:
        r = requests.get(cm_url, headers={"User-Agent": randomize_user_agent()})
    except Exception as e:
        logger.error(f"Error (CM), Unable to make web request, {e}")
        raise YikesAnError("Web request error (CM)!")

    try:
        soup = BeautifulSoup(r.text, "html.parser")
        res = soup.find_all(
            "script", attrs={"id": "__NEXT_DATA__", "type": "application/json"}
        )
        data = json.loads(res.pop().text)
        events = data["props"]["pageProps"]["data"]["events"]
    except Exception as e:
        logger.error(f"Error (CM), Unable to parse web data, {e}")
        raise YikesAnError("Web parsing error (CM)!")

    return events


def get_events_hb() -> list[dict]:
    dt_now = datetime.datetime.now()
    dt_fut = dt_now + relativedelta(years=2)
    hb_url = get_cred("HB_URL") + f"startDate={str(dt_now)[:10]}&endDate={str(dt_fut)[:10]}"

    try:
        r = requests.get(hb_url, headers={"User-Agent": randomize_user_agent()})
    except Exception as e:
        logger.error(f"Error (HB), Unable to make web request, {e}")
        raise YikesAnError("Web request error (HB)!")

    try:
        return r.json()
    except Exception as e:
        logger.error(f"Error (HB), JSON decode problem, {e}")
        raise YikesAnError("Web parsing error (HB)!")


def parse_events_cm(events: list[dict]) -> list[EventCM]:
    new_events = list()
    event_table = sb.table("events_cm").select("*").execute()

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
            EventCM(
                _id=_id,
                name=event.get("title"),
                when=when,
                room=event.get("room").get("title"),
                url=event.get("url"),
            )
        )

    return new_events


def parse_events_hb(events: list[dict]) -> list[EventHB]:
    new_events = list()
    event_table = sb.table("events_hb").select("*").execute()

    names = list(set(i["name"] for i in events))
    known_names = [i["name"] for i in event_table.data]

    for name in names:
        # skip if the named event is already known
        if name in known_names:
            continue
        dates = [i["eventDate"] for i in events if i["name"]==name]
        new_events.append(
            EventHB(
                name=name,
                dates=f"{dates[0][:10]} - {dates[-1][:10]}",
            )
        )

    return new_events


def execute_events_cm(new_events: list[EventCM]) -> None:
    msg = "NOMOFOMO ALERT CM!\n"
    for e in new_events:
        msg += f"{e.name}, {e.when}, {e.room}\n"
        _ = sb.table("events_cm").insert(e.__dict__).execute()
        logger.info(f"New event processed, {e.__dict__}")
    _ = send_sms(msg.strip())


def execute_events_hb(new_events: list[EventHB]) -> None:
    msg = "NOMOFOMO ALERT HB!\n"
    for e in new_events:
        msg += f"{e.name}, {e.dates}\n"
        _ = sb.table("events_hb").insert(e.__dict__).execute()
        logger.info(f"New event processed, {e.__dict__}")
    _ = send_sms(msg.strip())


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
    # cm
    run_cm = True
    try:
        events_cm = get_events_cm()
    except YikesAnError:
        run_cm = False
    if run_cm:
        new_events_cm = parse_events_cm(events_cm)
        if new_events_cm:
            logger.info("CM Parsing complete, NEW EVENTS found! Executing...")
            execute_events_cm(new_events_cm)
        else:
            logger.info("CM Parsing complete, no new events found")

    # hb
    run_hb = True
    try:
        events_hb = get_events_hb()
    except YikesAnError:
        run_hb = False
    if run_hb:
        new_events_hb = parse_events_hb(events_hb)
        if new_events_hb:
            logger.info("HB Parsing complete, NEW EVENTS found! Executing...")
            execute_events_hb(new_events_hb)
        else:
            logger.info("HB Parsing complete, no new events found")

    logger.info("nomofomo completed successfully")


if __name__ == "__main__":
    run()
