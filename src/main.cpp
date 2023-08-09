#include <Arduino.h>
#include <Bounce2.h>
#include <BLEDevice.h>
#include <BLEUtils.h>
#include "mbedtls/aes.h"

#define OLD_COLLECTION_PREFIX "QCAR"
#define CONTROL_SERVICE_UUID "0000fff0-0000-1000-8000-00805f9b34fb"
#define CONTROL_CHARACTERISTIC_UUID "d44bc439-abfd-45a2-b575-925416129600" /*- Write, Write no response */  //directions
#define BATTERYCHARACTERISTIC_UUID "d44bc439-abfd-45a2-b575-925416129601" /*- Write, Write no response */   //battery

#define SERVICE_UUID_1800 "00001800-0000-1000-8000-00805f9b34fb"
#define CHARACTERISTIC_UUID_1800 "00002a00-0000-1000-8000-00805f9b34fb" /* - Write no response */  //name

static BLEUUID controlUUID(CONTROL_SERVICE_UUID);             //Service UUID
static BLEUUID controCharlUUID(CONTROL_CHARACTERISTIC_UUID);  //Characteristic  UUID
static BLEUUID batteryUUID(BATTERYCHARACTERISTIC_UUID);       //Characteristic  UUID
String qcar_car_address_prefix = "00:3c";                     //Hardware Bluetooth MAC of my shell racing, use "nRF Connect" to inspect

static BLERemoteCharacteristic* pRemoteCharacteristic;
static BLERemoteCharacteristic* batteryCharacteristic;

BLEScan* pBLEScan;  //Name the scanning device as pBLEScan
BLEScanResults foundDevices;

static BLEAddress* Server_BLE_Address;
String scanned_car_address;

Bounce2::Button button = Bounce2::Button();

mbedtls_aes_context aes;
const uint8_t aes_key[16] = { 0x34, 0x52, 0x2A, 0x5B, 0x7A, 0x6E, 0x49, 0x2C, 0x08, 0x09, 0x0A, 0x9D, 0x8D, 0x2A, 0x23, 0xF8 };
uint8_t plain_data[16] = { 0x00, 0x43, 0x54, 0x4c, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00 };
uint8_t output_data[16];
uint8_t decrypted_data[16];

bool turbo_flag = 0;
bool paired = false;

#define PIN_FORWARD 15
#define PIN_BACKWARDS 13
#define PIN_LEFT 33
#define PIN_RIGHT 32
#define PIN_LIGHTS 35
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
    if (scanned_car_address.indexOf(qcar_car_address_prefix) != std::string::npos) {
      Server_BLE_Address = new BLEAddress(advertisedDevice.getAddress());
    }
  }
};

void craftBLErequest() {
  plain_data[4] = !digitalRead(PIN_FORWARD);
  plain_data[5] = !digitalRead(PIN_BACKWARDS);
  plain_data[6] = !digitalRead(PIN_LEFT);
  plain_data[7] = !digitalRead(PIN_RIGHT);
  plain_data[8] = digitalRead(PIN_LIGHTS);
  if (button.pressed()) {
    if (turbo_flag) {
      turbo_flag = 0;
    } else {
      turbo_flag = 1;
    }
    Serial.print("Turbo is ");
    Serial.println(turbo_flag);
  }
  if (turbo_flag) {
    plain_data[9] = 0x64;
  } else {
    plain_data[9] = 0x50;
  }
  mbedtls_aes_crypt_ecb(&aes, MBEDTLS_AES_ENCRYPT, plain_data, output_data);
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
  pRemoteCharacteristic = pRemoteService->getCharacteristic(controCharlUUID);
  batteryCharacteristic = pRemoteService->getCharacteristic(batteryUUID);
  batteryCharacteristic->registerForNotify(batteryNotifyCallback);
  if (pRemoteCharacteristic != nullptr)
    Serial.println(" - Found our characteristic");

  return true;
}
//When the BLE Server sends a new battery reading with the notify property
static void batteryNotifyCallback(BLERemoteCharacteristic* pBLERemoteCharacteristic,
                                  uint8_t* pData, size_t length, bool isNotify) {
  //store battery value
  mbedtls_aes_crypt_ecb(&aes, MBEDTLS_AES_DECRYPT, pData, decrypted_data);
  Serial.println(decrypted_data[4]);
}

void setup() {
  Serial.begin(115200);                                          //Start serial monitor
  Serial.println("ESP32 BLE Client - Shell Racing Car Remote");  //Intro message

  mbedtls_aes_init(&aes);
  mbedtls_aes_setkey_enc(&aes, aes_key, 128);
  mbedtls_aes_setkey_dec(&aes, aes_key, 128);
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
  button.attach(PIN_TURBO, INPUT_PULLUP);
  button.interval(5);
  button.setPressedState(LOW);
}

void loop() {
  button.update();
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
    bool response = false;
    craftBLErequest();
    if (pRemoteCharacteristic != nullptr) {
      pRemoteCharacteristic->writeValue(output_data, 16, response);
    }
    delay(100);
  }
}
