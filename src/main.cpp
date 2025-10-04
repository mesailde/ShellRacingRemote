#include <Arduino.h>
#include <Bounce2.h>
#include <BLEDevice.h>
#include <BLEUtils.h>

#define CAR_NAME_PREFIX "SL-"
#define CONTROL_SERVICE_UUID "0000fff0-0000-1000-8000-00805f9b34fb"
#define CONTROL_CHARACTERISTIC_UUID "0000fff1-0000-1000-8000-00805f9b34fb"
#define TELEMETRY_CHARACTERISTIC_UUID "0000fff2-0000-1000-8000-00805f9b34fb"
#define BATTERY_SERVICE_UUID "0000180f-0000-1000-8000-00805f9b34fb"
#define BATTERY_CHARACTERISTIC_UUID "00002a19-0000-1000-8000-00805f9b34fb"

static BLEUUID controlUUID(CONTROL_SERVICE_UUID);
static BLEUUID controlCharUUID(CONTROL_CHARACTERISTIC_UUID);
static BLEUUID telemetryCharUUID(TELEMETRY_CHARACTERISTIC_UUID);
static BLEUUID batteryServiceUUID(BATTERY_SERVICE_UUID);
static BLEUUID batteryCharUUID(BATTERY_CHARACTERISTIC_UUID);
String car_name_prefix = CAR_NAME_PREFIX;

static BLERemoteCharacteristic* controlCharacteristic;
static BLERemoteCharacteristic* telemetryCharacteristic;
static BLERemoteCharacteristic* batteryCharacteristic;

BLEScan* pBLEScan;  //Name the scanning device as pBLEScan
BLEScanResults foundDevices;

static BLEAddress* Server_BLE_Address;
String scanned_car_address;

Bounce2::Button button_turbo = Bounce2::Button();
Bounce2::Button button_light = Bounce2::Button();

uint8_t control_payload[8] = { 1, 0, 0, 0, 0, 0, 0, 0 };

bool light_flag = false;
bool turbo_flag = false;
bool donut_flag = false;
bool paired = false;

#define PIN_FORWARD 15
#define PIN_BACKWARDS 13
#define PIN_LEFT 33
#define PIN_RIGHT 32
#define PIN_LIGHTS 14
#define PIN_TURBO 16

class MyClientCallback : public BLEClientCallbacks {
  void onConnect(BLEClient* pClient) {
    paired = true;
    Serial.println(" onConnect Connected to Server");
  }
  void onDisconnect(BLEClient* pClient) {
    paired = false;
    Server_BLE_Address = NULL;
    Serial.println("Disconnected from Server");
  }
};

class MyAdvertisedDeviceCallbacks : public BLEAdvertisedDeviceCallbacks {
  void onResult(BLEAdvertisedDevice advertisedDevice) {
    scanned_car_address = advertisedDevice.getAddress().toString().c_str();
    Serial.println(scanned_car_address);
    if (advertisedDevice.haveName() && advertisedDevice.getName().find(car_name_prefix.c_str()) == 0) {
      Server_BLE_Address = new BLEAddress(advertisedDevice.getAddress());
    } else if (scanned_car_address.indexOf(car_name_prefix) != std::string::npos) {
      Server_BLE_Address = new BLEAddress(advertisedDevice.getAddress());
    }
  }
};

void craftBLErequest() {
  control_payload[0] = 1;  // drive mode (1/2)
  control_payload[1] = !digitalRead(PIN_FORWARD);
  control_payload[2] = !digitalRead(PIN_BACKWARDS);
  control_payload[3] = !digitalRead(PIN_LEFT);
  control_payload[4] = !digitalRead(PIN_RIGHT);
  control_payload[5] = light_flag ? 1 : 0;
  control_payload[6] = turbo_flag ? 1 : 0;
  control_payload[7] = donut_flag ? 1 : 0;
}

// Telemetry notifications from 0xFFF2 (currently unknown payload)
static void telemetryNotifyCallback(BLERemoteCharacteristic* /*pBLERemoteCharacteristic*/,
                                    uint8_t* pData, size_t length, bool /*isNotify*/) {
  Serial.print("Telemetry: ");
  for (size_t i = 0; i < length; ++i) {
    if (i > 0) Serial.print(" ");
    Serial.printf("%02X", pData[i]);
  }
  Serial.println();
}

// Battery notifications/readouts from 0x2A19
static void batteryNotifyCallback(BLERemoteCharacteristic* /*pBLERemoteCharacteristic*/,
                                  uint8_t* pData, size_t length, bool /*isNotify*/) {
  if (length >= 1) {
    Serial.print("Battery: ");
    Serial.print(pData[0]);
    Serial.println("%");
  }
}

bool connectToserver(BLEAddress pAddress) {
  BLEClient* pClient = BLEDevice::createClient();
  Serial.println(" - Created client");
  pClient->setClientCallbacks(new MyClientCallback());
  // Connect to the BLE Server.
  pClient->connect(pAddress);
  Serial.println(" - Connected to Shell Race Car");

  // Obtain a reference to the service we are after in the remote BLE server.
  BLERemoteService* pRemoteService = pClient->getService(controlUUID);
  if (pRemoteService != nullptr) {
    Serial.println(" - Found our service");
  } else {
    return false;
  }
  std::map<std::string, BLERemoteCharacteristic*>* cm = pRemoteService->getCharacteristics();
  std::map<std::string, BLERemoteCharacteristic*>::iterator it;
  for (it = cm->begin(); it != cm->end(); it++) {
    Serial.print(it->first.c_str());
    Serial.print(":");
    Serial.print(it->second->toString().c_str());
    Serial.println("");
  }
  // Obtain a reference to the characteristic in the service of the remote BLE server.
  controlCharacteristic = pRemoteService->getCharacteristic(controlCharUUID);
  telemetryCharacteristic = pRemoteService->getCharacteristic(telemetryCharUUID);
  if (controlCharacteristic != nullptr) {
    Serial.println(" - Found control characteristic");
  }
  if (telemetryCharacteristic != nullptr) {
    telemetryCharacteristic->registerForNotify(telemetryNotifyCallback);
    Serial.println(" - Subscribed to telemetry characteristic");
  }

  BLERemoteService* pBatteryService = pClient->getService(batteryServiceUUID);
  if (pBatteryService != nullptr) {
    batteryCharacteristic = pBatteryService->getCharacteristic(batteryCharUUID);
    if (batteryCharacteristic != nullptr) {
      batteryCharacteristic->registerForNotify(batteryNotifyCallback);
      Serial.println(" - Registered for battery notifications");
    }
  }

  return true;
}

void setup() {
  Serial.begin(115200);                                          //Start serial monitor
  Serial.println("ESP32 BLE Client - Shell Racing Car Remote");  //Intro message

  BLEDevice::init("");
  pBLEScan = BLEDevice::getScan();                                            //create new scan
  pBLEScan->setAdvertisedDeviceCallbacks(new MyAdvertisedDeviceCallbacks());  //Call the class that is defined above
  pBLEScan->setActiveScan(true);                                              //active scan uses more power, but get results faster

  // Make them with interl pullups
  pinMode(PIN_FORWARD, INPUT_PULLUP);
  pinMode(PIN_BACKWARDS, INPUT_PULLUP);
  pinMode(PIN_LEFT, INPUT_PULLUP);
  pinMode(PIN_RIGHT, INPUT_PULLUP);
  pinMode(PIN_LIGHTS, INPUT_PULLUP);
  button_turbo.attach(PIN_TURBO, INPUT_PULLUP);
  button_turbo.interval(5);
  button_turbo.setPressedState(LOW);
  button_light.attach(PIN_LIGHTS, INPUT_PULLUP);
  button_light.interval(5);
  button_light.setPressedState(LOW);
}

void loop() {
  button_turbo.update();
  if (button_turbo.pressed()) {
    turbo_flag = !turbo_flag;
    Serial.print("Turbo is ");
    Serial.println(turbo_flag);
  }
  button_light.update();
  if (button_light.pressed()) {
    light_flag = !light_flag;
    Serial.print("Lights are ");
    Serial.println(light_flag);
  }
  if (paired == false) {
    foundDevices = pBLEScan->start(3);  //Scan for 3 seconds to find the Shell Car
    while (foundDevices.getCount() >= 1) {
      if (Server_BLE_Address != NULL && paired == false) {
        Serial.println("Found Device :-)... connecting to Server as client");
        if (connectToserver(*Server_BLE_Address)) {
          paired = true;
          Serial.println("Paired successfully");
          break;
        } else {
          Serial.println("Pairing failed");
          break;
        }
      }
      if (paired == true) {
        Serial.println("Our device went out of range");
        paired = false;
        ESP.restart();
        break;
      } else {
        Serial.println("We have some other BLe device in range");
        break;
      }
    }
  } else {
    craftBLErequest();
    if (controlCharacteristic != nullptr) {
      controlCharacteristic->writeValue(control_payload, sizeof(control_payload), false);
    }
    delay(100);
  }
}
