"""
    This is a simple script to demonstrate scripting and automation for
    Cisco DNAC/Catalyst Center API.

    This is for simplification and demo purposes only, and does not indicate best practices. 

    For instance:
        - generic Exception try catch statements are not best practice.
        - disabling InsecureRequestWarning
        - use of global space for session definition
        - static array/list position referencing without conditional logic checks
        - accessing dictionary keys without use of ['key'] instead of .get() with defaults
        - static sleep/time delays instead of exponential backoff logic
"""

import requests
import os
from dotenv import load_dotenv
import urllib3
from time import sleep

urllib3.disable_warnings(
    urllib3.exceptions.InsecureRequestWarning
)  # Disable SSL certificate warnings

load_dotenv()
CCC_URL = os.environ["CCC_URL"]
CCC_UN = os.environ["CCC_UN"]
CCC_PW = os.environ["CCC_PW"]

def auth_session() -> str:
    """Obtain Token from server for subsequent requests

    Raises:
        Exception: When empty response is recieved

    Returns:
        str: Token
    """
    auth_url = f"{CCC_URL}/dna/system/api/v1/auth/token"
    r = requests.post(url=auth_url, auth=(CCC_UN, CCC_PW), verify=False)
    r.raise_for_status()
    if r.text == "":
        raise Exception("Unable to obtain token.")
    token = r.json()["Token"]
    return token

# Session Setup
s = requests.session()
s.verify = False
token = auth_session()
s.headers.update(
    {
        "X-Auth-Token": token,
        "content-type": "application/json",
        "Accept": "application/json",
    }
)

def get_client_details(mac_address: str) -> tuple[str, str]:
    """Get client details based on MAC Address

    Args:
        mac_address (str): MAC address of device in the form of AA:AA:AA..

    Raises:
        Exception: Empty response from the server
        Exception: Unable to obtain parent UUID from response
        Exception: Unable to obtain parent UUID from response

    Returns:
        tuple[str, str]: Connected Interface Name, and Parent Device UUID
    """
    query = f"?macAddress={mac_address}"
    url = f"{CCC_URL}/dna/intent/api/v1/client-detail{query}"
    r = s.get(url=url)
    r.raise_for_status()
    if r.text == "":
        raise Exception("Empty Response from the server.")
    json_resp = r.json()
    interface_name = json_resp["detail"]["port"]
    try:
        parent_device_uuid = json_resp.get("detail", {}).get("connectedDevice", {})[0]['id']
    except KeyError:
        pass
    if not parent_device_uuid: # fallback method best effort
        try:
            nodes = json_resp.get("topology", {}).get("nodes", {})
            if len(nodes) <= 1:
                raise Exception("Unable to get parent device UUID from client details")
            parent_device_uuid = nodes[1].get("id", {})
        except (KeyError, IndexError):
            raise Exception("Unable to get parent device UUID from client details")
    return interface_name, parent_device_uuid

def get_interface_details(
    parent_device_uuid: str, interface_name: str
) -> tuple[str, str]:
    """Get interface details on parent device based on interface name

    Args:
        parent_device_uuid (str): UUID of parent device
        interface_name (str): Interface name e.g "GigabitEthernet1/0/3"

    Raises:
        Exception: Empty response from the server
        Exception: Unable to locate interface details

    Returns:
        tuple[str, str]: Interface UUID, and Interface Status (UP/DOWN)
    """
    query = f"?name={interface_name}"
    url = f"{CCC_URL}/dna/intent/api/v1/interface/network-device/{parent_device_uuid}/interface-name{query}"
    r = s.get(url=url)
    r.raise_for_status()
    if r.text == "":
        raise Exception("Empty Response from the server.")
    json_resp = r.json()
    interface_uuid = json_resp.get("response", {}).get("id", {})
    current_interface_status = json_resp.get("response", {}).get("adminStatus", {})
    if not interface_uuid or not current_interface_status:
        raise Exception("Unable to locate interface details of either: UUID or status")
    return interface_uuid, current_interface_status

def lookup_task(task_id: str) -> dict:
    """Looks up a task in DNAC based on task_id

    Args:
        task_id (str): ID of the task

    Raises:
        Exception: Empty response from the server

    Returns:
        dict: Status of the submitted task
    """
    url = f"{CCC_URL}/dna/intent/api/v1/tasks/{task_id}"
    r = s.get(url=url)
    r.raise_for_status()
    if r.text == "":
        raise Exception("Empty Response from the server.")
    return r.json()

def interface_shut_no_shut(
    interface_uuid: str, current_interface_status: str, mode: str = "Deploy"
) -> None:
    """Perform a shut no shut (restart) of interface based on interface status

    Args:
        interface_uuid (str): UUID of interface
        current_interface_status (str): Status of Interface e.g UP/DOWN
        mode (str, optional): Determines if dry run should be executed or
        if changes should be deployed to perform a shut no shut. Defaults to "Deploy".

    Raises:
        Exception: Empty Response from the server
        Exception: Interface status update failed
        Exception: Empty Response from the server
        Exception: Interface status update failed
        Exception: Empty Response from the server
    """
    query = f"?deploymentMode={mode}"
    url = f"{CCC_URL}/dna/intent/api/v1/interface/{interface_uuid}{query}"
    if current_interface_status == "DOWN":
        r1 = s.put(url=url, json={"adminStatus": "UP"})
        r1.raise_for_status()
        if r1.text == "":
            raise Exception("Empty Response from the server.")
        resp1 = r1.json()
        while True:
            task_details = lookup_task(task_id=resp1["response"]["taskId"])
            if task_details["response"]["status"] == "PENDING":
                sleep(1)
                continue
            elif task_details["response"]["status"] == "SUCCESS":
                break
            elif task_details["response"]["status"] != "SUCCESS":
                raise Exception("Interface update task failed")
    elif current_interface_status == "UP":
        r1 = s.put(url=url, json={"adminStatus": "DOWN"})
        r1.raise_for_status()
        if r1.text == "":
            raise Exception("Empty Response from the server.")
        resp1 = r1.json()
        while True:
            task_details = lookup_task(task_id=resp1["response"]["taskId"])
            if task_details["response"]["status"] == "PENDING":
                sleep(1)
                continue
            elif task_details["response"]["status"] == "SUCCESS":
                break
            else:
                raise Exception("Interface update task failed")
        try:
            r2 = s.put(url=url, json={"adminStatus": "UP"})
            r2.raise_for_status()
            if r2.text == "":
                raise Exception("Empty Response from the server.")
            return
        except requests.exceptions.HTTPError as e:
            if "No change in setting" in e.response.text:
                return
            else:
                raise
    return

def port_bounce(mac_address: str, mode: str = "Deploy") -> None:
    """Performs a shut no shut operation on a POE device based on MAC address

    Args:
        mac_address (str): MAC address of the device to port bounce
        mode (str, optional): Option to dry run vs deploy changes. Defaults to "Deploy".
    """
    interface_name, parent_device_uuid = get_client_details(
        mac_address=mac_address
    )
    interface_uuid, current_interface_status = get_interface_details(
        parent_device_uuid=parent_device_uuid,
        interface_name=interface_name
    )
    interface_shut_no_shut(
        interface_uuid=interface_uuid,
        current_interface_status=current_interface_status
    )
    return

def main() -> None:
    """
    Main entry point of the program. This is just personal convention
    """
    mac_address = "00:A2:89:AA:AA:AA" # Fake MAC Address for demo
    port_bounce(mac_address=mac_address)


if __name__ == "__main__":
    main()
