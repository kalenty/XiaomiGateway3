import asyncio

from .base import XGateway
from ..device import XDevice
from ..mini_mqtt import MQTTMessage


# noinspection PyMethodMayBeStatic,PyUnusedLocal
class MIoTGateway(XGateway):
    def miot_on_mqtt_publish(self, msg: MQTTMessage):
        if msg.topic in ("miio/report", "central/report"):
            if b'"properties_changed"' in msg.payload:
                self.miot_process_properties(msg.json["params"])
            elif b'"event_occured"' in msg.payload:
                self.miot_process_event(msg.json["params"])
        elif msg.topic == "miio/command_ack":
            # check if it is response from `get_properties` command
            result = msg.json.get("result")
            if isinstance(result, list) and any(
                "did" in i and "siid" in i and "value" in i
                for i in result
                if isinstance(i, dict)
            ):
                self.miot_process_properties(result)

    def miot_process_properties(self, params: list):
        """Can receive multiple properties from multiple devices.
        data = [{'did':123,'siid':2,'piid':1,'value':True,'tid':158}]
        """
        # convert miio response format to multiple responses in lumi format
        devices: dict[str, list] = {}
        for item in params:
            if not (device := self.devices.get(item["did"])):
                continue

            if (seq := item.get("tid")) is not None:
                if seq == device.extra.get("seq"):
                    continue
                device.extra["seq"] = seq

            devices.setdefault(item["did"], []).append(item)

        for did, params in devices.items():
            device = self.devices[did]
            if self.stats_domain:
                device.dispatch({device.type: True})
            device.on_report(params, self)

    def miot_process_event(self, item: dict):
        # {"did":"123","siid":8,"eiid":1,"tid":123,"ts":123,"arguments":[]}
        if not (device := self.devices.get(item["did"])):
            return

        if (seq := item.get("tid")) is not None:
            if seq == device.extra.get("seq"):
                return
            device.extra["seq"] = seq

        if self.stats_domain:
            device.dispatch({device.type: True})

        device.on_report(item, self)

    async def miot_send(self, device: XDevice, method: str, data: dict):
        assert method in ("set_properties", "get_properties")

        payload = {"method": method, "params": data["mi_spec"]}
        for item in payload["params"]:
            item["did"] = device.did

        # check if we can send command via any second gateway
        gw2 = next((gw for gw in device.gateways if gw != self and gw.available), None)
        if gw2:
            await self.mqtt_publish_multiple(device, payload, gw2)
        else:
            await self.mqtt.publish("miio/command", payload)

    async def mqtt_publish_multiple(
        self, device: XDevice, payload: dict, gw2, delay: float = 1.0
    ):
        fut = asyncio.get_event_loop().create_future()
        device.add_listener(fut.set_result)
        await self.mqtt.publish("miio/command", payload)
        try:
            async with asyncio.timeout(delay):
                await fut
        except TimeoutError:
            await gw2.mqtt.publish("miio/command", payload)
        finally:
            device.remove_listener(fut.set_result)