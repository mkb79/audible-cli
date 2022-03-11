"""Converts the credentials.json file from OpenAudible >= v2.4 beta to an
audible-cli auth file. The credentials.json file from OpenAudible leaves 
unchanged, so you can use one device registration for OpenAudible and 
audible-cli."""


import json
import pathlib

import audible
import click
from audible_cli.config import pass_session


def extract_data_from_file(credentials):
    origins = {}
    for k, v in credentials.items():
        if k == "active_device":
            continue
        origin = v["details"]["response"]["success"]
        origin["additionnel"] = {
            "expires": v["expires"],
            "region": v["region"].lower()
        }
        origins.update({k: origin})
    return origins


def make_auth_file(fn, origin):
    tokens = origin["tokens"]
    adp_token = tokens["mac_dms"]["adp_token"]
    device_private_key = tokens["mac_dms"]["device_private_key"]
    store_authentication_cookie = tokens["store_authentication_cookie"]
    access_token = tokens["bearer"]["access_token"]
    refresh_token = tokens["bearer"]["refresh_token"]
    expires = origin["additionnel"]["expires"]

    extensions = origin["extensions"]
    device_info = extensions["device_info"]
    customer_info = extensions["customer_info"]

    website_cookies = dict()
    for cookie in tokens["website_cookies"]:
        website_cookies[cookie["Name"]] = cookie["Value"].replace(r'"', r'')

    data = {
        "adp_token": adp_token,
        "device_private_key": device_private_key,
        "access_token": access_token,
        "refresh_token": refresh_token,
        "expires": expires,
        "website_cookies": website_cookies,
        "store_authentication_cookie": store_authentication_cookie,
        "device_info": device_info,
        "customer_info": customer_info,
        "locale": origin["additionnel"]["region"]
    }
    auth = audible.Authenticator()
    auth._update_attrs(**data)
    return auth


@click.command("convert-oa-file")
@click.option(
    "--input", "-i",
    type=click.Path(exists=True, file_okay=True),
    multiple=True,
    help="OpenAudible credentials.json file")
@pass_session
def cli(session, input):
    """Converts a OpenAudible credential file to a audible-cli auth file
    
    Stores the auth files in app dir"""
    fdata = pathlib.Path(input).read_text("utf-8")
    fdata = json.loads(fdata)
    
    x = extract_data_from_file(fdata)
    for k, v in x.items():
        app_dir = pathlib.Path(session.get_app_dir())
        fn = app_dir / pathlib.Path(k).with_suffix(".json")
        auth = make_auth_file(fn, v)
        auth.to_file(fn)

