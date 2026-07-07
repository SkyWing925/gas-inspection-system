"""Output: cloud upload (Huawei IoTDA via MQTT).

Wraps iotda_test.py logic. Optional and non-blocking — failures are
logged but never raised to the pipeline.
"""

import os
import json
import base64
import time
import hmac
import hashlib
import ssl
import logging

logger = logging.getLogger("gas_detection.cloud")


class CloudUploader:
    """Upload detection results to Huawei Cloud IoTDA."""

    def __init__(self, device_id: str, secret: str, server: str, port: int):
        self.device_id = device_id
        self.secret = secret
        self.server = server
        self.port = port
        self._connected = False

    def send_alert(self, level: str, location: int, note: str) -> bool:
        """Send text alert to IoTDA."""
        try:
            import paho.mqtt.client as mqtt

            timestamp = time.strftime("%Y%m%d%H", time.gmtime())
            client_id = f"{self.device_id}_0_0_{timestamp}"
            password = hmac.new(
                timestamp.encode(), self.secret.encode(), hashlib.sha256,
            ).hexdigest()

            connected = [False]

            def on_connect(client, userdata, flags, rc, properties=None):
                try:
                    rc_int = rc.value
                except AttributeError:
                    rc_int = rc
                if rc_int == 0:
                    connected[0] = True

            client = mqtt.Client(
                client_id=client_id, protocol=mqtt.MQTTv311,
                callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
            )
            client.username_pw_set(self.device_id, password)
            client.tls_set(cert_reqs=ssl.CERT_REQUIRED, tls_version=ssl.PROTOCOL_TLSv1_2)
            client.on_connect = on_connect

            client.connect(self.server, self.port, keepalive=60)
            client.loop_start()
            time.sleep(2)

            if not connected[0]:
                logger.warning("Cloud upload: failed to connect")
                client.loop_stop()
                return False

            now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            topic = f"$oc/devices/{self.device_id}/sys/messages/up"
            payload = json.dumps({
                "type": "alert",
                "level": level,
                "location": location,
                "note": note,
                "ts": now,
            }, ensure_ascii=False)

            client.publish(topic, payload, qos=1)
            time.sleep(1)
            client.loop_stop()
            client.disconnect()
            logger.info(f"Cloud alert sent: [{level}] loc={location}")
            return True

        except ImportError:
            logger.debug("paho-mqtt not installed, cloud upload skipped")
            return False
        except Exception as e:
            logger.warning(f"Cloud upload failed: {e}")
            return False

    def send_image(self, image_path: str, level: str, location: int,
                   note: str) -> bool:
        """Send image with base64 encoding to IoTDA."""
        if not os.path.exists(image_path):
            logger.warning(f"Image not found: {image_path}")
            return False

        with open(image_path, "rb") as f:
            raw = f.read()

        if len(raw) > 900_000:
            logger.warning(f"Image too large: {len(raw)} bytes")
            return False

        try:
            import paho.mqtt.client as mqtt

            timestamp = time.strftime("%Y%m%d%H", time.gmtime())
            client_id = f"{self.device_id}_0_0_{timestamp}"
            password = hmac.new(
                timestamp.encode(), self.secret.encode(), hashlib.sha256,
            ).hexdigest()

            connected = [False]

            def on_connect(client, userdata, flags, rc, properties=None):
                try:
                    rc_int = rc.value
                except AttributeError:
                    rc_int = rc
                if rc_int == 0:
                    connected[0] = True

            client = mqtt.Client(
                client_id=client_id, protocol=mqtt.MQTTv311,
                callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
            )
            client.username_pw_set(self.device_id, password)
            client.tls_set(cert_reqs=ssl.CERT_REQUIRED, tls_version=ssl.PROTOCOL_TLSv1_2)
            client.on_connect = on_connect

            client.connect(self.server, self.port, keepalive=60)
            client.loop_start()
            time.sleep(2)

            if not connected[0]:
                client.loop_stop()
                return False

            now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            topic = f"$oc/devices/{self.device_id}/sys/messages/up"
            payload = json.dumps({
                "type": "image",
                "name": os.path.basename(image_path),
                "size": len(raw),
                "data": base64.b64encode(raw).decode("ascii"),
                "level": level,
                "location": location,
                "note": note,
                "ts": now,
            })

            client.publish(topic, payload, qos=1)
            time.sleep(1)
            client.loop_stop()
            client.disconnect()
            logger.info(f"Cloud image sent: {os.path.basename(image_path)}")
            return True

        except Exception as e:
            logger.warning(f"Cloud image upload failed: {e}")
            return False
