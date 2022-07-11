import os
import threading
import uuid
from collections import defaultdict
from dataclasses import dataclass
from os.path import splitext
from pathlib import Path
from typing import Optional
from urllib import request
from urllib.parse import urlparse

import yaml
from dotenv import load_dotenv
from slack_bolt import App, Ack, Respond
from slack_bolt.adapter.socket_mode import SocketModeHandler
from slack_sdk import WebClient

import log

log.logging_level = log.INFO

load_dotenv()

SLACK_BOT_TOKEN = os.getenv("SLACK_BOT_TOKEN")
SLACK_APP_TOKEN = os.getenv("SLACK_APP_TOKEN")

KNOWN_CANDIDATES_YAML = Path(__file__).absolute().parent.joinpath("data", "known_candidates.yaml")
OPEN_POSITIONS_YAML = Path(__file__).absolute().parent.joinpath("data", "open_positions.yaml")
PARTY_LEADS_FILE_PATH = Path(__file__).absolute().parent.joinpath("data", "leads", "leads.yaml")
MAX_COMMAND_LENGTH = 2 ** 8
CANDIDATE_LIMIT = 9

app = App(token=SLACK_BOT_TOKEN)  # initializes your app with your bot token and socket mode handler

known_candidates = {}
submitted_candidates = set()
done_submissions_by_user = defaultdict(lambda: defaultdict(dict))
submissions_by_token = {}
open_positions = []
current_token_by_user_id = {}
store_lock = threading.Lock()


@dataclass
class Submission:
    referer: Optional[str]
    candidate: Optional[str]
    url: Optional[str]
    position: Optional[str]
    ts: Optional[str]  # the timestamp of the submit command, used for deletion
    extra_info: Optional[str]
    cv_filename: Optional[str]
    done: bool

    def __init__(self):
        self.cv_filename = self.extra_info = self.ts = self.referer = self.candidate = self.position = self.url = None
        self.done = False


def canonicalize_name(name):
    """ removed non-alpha strings from the name and lowercase's it """
    return ''.join(c for c in name if c.isalpha()).lower()


with open(KNOWN_CANDIDATES_YAML, "r") as fp:
    try:
        known_candidates = {candidate: canonicalize_name(candidate) for candidate in yaml.safe_load(fp)}
    except yaml.YAMLError as e:
        log.warn(f"Error '{e}' parsing list of known candidates '{KNOWN_CANDIDATES_YAML}'. "
                 f"Known candidates list will be empty.")

with open(OPEN_POSITIONS_YAML, "r") as fp:
    try:
        open_positions = yaml.safe_load(fp)
    except yaml.YAMLError as e:
        log.warn(f"Error '{e}' parsing list of known candidates '{OPEN_POSITIONS_YAML}'. "
                 f"Known candidates list will be empty.")


# The partybot-report command shows all submissions for a user
@app.command("/partybot-report")
def partybot_report(ack: Ack, command: dict, client: WebClient):
    ack()  # acknowledge command request

    user_name = command['user_name']
    user_id = command['user_id']

    submissions = {}
    for position, submissions_by_name in done_submissions_by_user[user_name].items():
        position_submissions = [{"candidate": submission.candidate} for name, submission in submissions_by_name.items()]
        submissions[position] = position_submissions

    text = yaml.dump(submissions)
    client.chat_postMessage(
        channel=user_id,
        text=text,
        blocks=[
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": text
                }
            }
        ]
    )


@app.command("/partybot-submit")
def partybot_submit(ack: Ack, respond: Respond, command: dict, client: WebClient):
    """ The partybot-submit command receives the candidate submission """
    ack()  # acknowledge command request

    if len(command['text']) > MAX_COMMAND_LENGTH:
        respond(text=f"Maximal command length allowed is *{MAX_COMMAND_LENGTH}*. "
                     f"Stop messing around and go get me some Leads 😠")
        return

    user_name = command['user_name']
    user_id = command['user_id']

    split_command = command['text'].split(" ")
    candidate_url, candidate_name = split_command[0], " ".join(split_command[1:])
    candidate_url = candidate_url.replace("<", "").replace(">", "")
    if "|" in candidate_url:
        candidate_url = candidate_url.split("|")[0]

    if not urlparse(candidate_url).scheme:
        log.warn(f"malformed url: '{candidate_url}' submitted by user {user_name}")
        respond(text=f"The submitted LinkedIn url *_{candidate_url}_* does not seem valid 🧐 "
                     f"I hope you know what you're doing.")

    if not candidate_name:
        respond(text=f"Empty candidate name submitted. Please see /partybot-submit syntax")
        return

    global known_candidates
    canonical_name = canonicalize_name(candidate_name)
    if canonical_name in known_candidates.values():
        respond(text=f"Sorry, candidate *{candidate_name}* is already in candidate pool 😐")
        return

    global submitted_candidates
    if canonical_name in submitted_candidates:
        respond(text=f"Damn! someone else beat you to the punch! Candidate *{candidate_name}* was already submitted 💔")
        return

    submitted_candidates.add(canonical_name)

    global submissions_by_token
    token = str(uuid.uuid4())
    submission = Submission()
    submission.referer = user_name
    submission.candidate = candidate_name
    submission.url = candidate_url
    log.info(f"Submission '{submission}' initiated")
    submissions_by_token[token] = submission
    current_token_by_user_id[user_id] = token

    # we use client instead of respond as it allows us to track the message timestamp and later on delete it
    submission.ts = client.chat_postMessage(
        channel=user_id,
        text=f"What position are you submitting {candidate_name} to?",
        blocks=[
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"Which position are you submitting *{candidate_name}* to?"
                }
            },
            {
                "type": "actions",
                "elements": [
                    {
                        "type": "radio_buttons",
                        "options": [
                            {
                                "text": {
                                    "type": "plain_text",
                                    "text": position,
                                    "emoji": True
                                },
                                "value": f"{token}:{position}"
                            }
                            for position in open_positions
                        ],
                        "action_id": "pick_position"
                    }
                ]
            },
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": "Click to cancel submission"
                },
                "accessory": {
                    "type": "button",
                    "text": {
                        "type": "plain_text",
                        "text": "Cancel Submission",
                        "emoji": True
                    },
                    "value": f"{token}:cancel",
                    "action_id": "cancel"
                }
            }
        ])["ts"]


def finish_submission(user_id: str, submission: Submission, client: WebClient):
    submission.done = True
    done_submissions_by_user[submission.referer][submission.position][submission.candidate] = submission
    text = f"Submission done for *{submission.candidate}* 💪"
    client.chat_postMessage(channel=user_id, text=text,
                            blocks=[
                                {
                                    "type": "section",
                                    "text": {
                                        "type": "mrkdwn",
                                        "text": text
                                    }
                                }
                            ])
    client.chat_delete(
        channel=user_id,
        ts=submission.ts
    )
    save()


def save():
    with store_lock:
        submissions = []
        for submissions_by_positions in done_submissions_by_user.values():
            for submissions_by_name in submissions_by_positions.values():
                for submission in submissions_by_name.values():
                    submissions.append(vars(submission))

        with open(str(PARTY_LEADS_FILE_PATH), "w") as fp:
            yaml.safe_dump(submissions, fp)


@app.action({
    "action_id": "pick_position"
})
def pick_position(ack: Ack, body: dict, client: WebClient):
    ack()

    token = body["actions"][0]["selected_option"]["value"].split(":")[0]
    user_id = body["user"]["id"]
    user_name = body["user"]["username"]
    position = body["actions"][0]["selected_option"]["text"]["text"]

    if len(done_submissions_by_user[user_name][position]) >= CANDIDATE_LIMIT:
        client.chat_postMessage(channel=user_id,
                                text=f"You've reached your limit of submissions for the *{position}* position. "
                                     f"Please contact the Party manager if you must submit.")
        return

    submission = submissions_by_token.get(token)
    if not submission:
        log.error(f"No open submission for user '{user_name}'")
        client.chat_postMessage(channel=user_id, text="Oops 😨 Something went wrong. Please contact Nimrod.")
        return

    candidate_name = submission.candidate
    text = f"Got it. Position *{position}* selected for candidate *{candidate_name}* ✅"
    client.chat_postMessage(channel=user_id, text=text,
                            blocks=[
                                {
                                    "type": "section",
                                    "text": {
                                        "type": "mrkdwn",
                                        "text": text
                                    }
                                }
                            ])

    submission.position = position
    log.info(f"Position selected for submission '{submission}'")

    submission.ts = body["container"]["message_ts"]
    finish_submission(user_id, submission, client)


@app.action({
    "action_id": "cancel"
})
def cancel_submission(ack: Ack, body: dict, client: WebClient):
    ack()
    token = body["actions"][0]["value"].split(":")[0]
    user_id = body["user"]["id"]

    global submissions_by_token
    global submitted_candidates
    submission = submissions_by_token.get(token)
    canonical_name = canonicalize_name(submission.candidate)
    submitted_candidates.remove(canonical_name)

    text = f"Submission canceled for *{submission.candidate}* 👋"
    client.chat_postMessage(channel=user_id, text=text,
                            blocks=[
                                {
                                    "type": "section",
                                    "text": {
                                        "type": "mrkdwn",
                                        "text": text
                                    }
                                }
                            ])
    client.chat_delete(
        channel=user_id,
        ts=submission.ts
    )
    log.info(f"Submission '{submission}' canceled")


@app.event("message")
def extra_info_message(body: dict, client: WebClient):
    global current_token_by_user_id

    user_id = body["event"].get("user")
    if not user_id:  # bot's own message
        return

    token = current_token_by_user_id.get(user_id)
    submission = submissions_by_token.get(token)

    if not submission:
        log.info(f"User {user_id} sent unrelated message {body['event']['text']}")
        return

    if submission.done:
        return

    file_name = None
    if body.get("event", {}).get("subtype") == "file_share":
        file_url = body["event"]["files"][0]["url_private_download"]
        file_name = body["event"]["files"][0]["name"]
        extension = splitext(file_name)[1]
        opener = request.build_opener()
        opener.addheaders = [("Authorization", f"Bearer {SLACK_BOT_TOKEN}")]
        request.install_opener(opener)
        submission.cv_filename = str(request.urlretrieve(file_url, Path("data", "leads", f"{uuid.uuid4()}{extension}"))[0])

    submission.extra_info = extra_info = body["event"]["text"]
    text = f"Info saved for candidate *{submission.candidate}* (" + \
           (f"CV: _{file_name}_ " if submission.cv_filename else "") + \
           (f"extra info: {extra_info}" if extra_info else "") + ") ✅"
    client.chat_postMessage(channel=user_id, text=text,
                            blocks=[
                                {
                                    "type": "section",
                                    "text": {
                                        "type": "mrkdwn",
                                        "text": text
                                    }
                                }
                            ])

    if submission.position:  # all selected, submission done!
        finish_submission(user_id, submission, client)


if __name__ == "__main__":
    SocketModeHandler(app, SLACK_APP_TOKEN).start()
