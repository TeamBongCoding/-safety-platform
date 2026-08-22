#include <SoftwareSerial.h>

// HM-10 TXD -> Arduino D4
// Arduino D5 -> voltage divider -> HM-10 RXD
SoftwareSerial BTSerial(4, 5); // Arduino RX, TX

const unsigned long SCAN_TIMEOUT_MS = 15000;
const unsigned long RESCAN_DELAY_MS = 750;
const char SCAN_END[] = "OK+DISCE";

bool scanning = false;
unsigned long scanStartedAt = 0;
unsigned long nextScanAt = 0;
byte markerIndex = 0;

void forwardFor(unsigned long durationMs) {
  const unsigned long startedAt = millis();
  while (millis() - startedAt < durationMs) {
    while (BTSerial.available()) Serial.write(BTSerial.read());
  }
}

void sendCommand(const __FlashStringHelper* command, unsigned long waitMs) {
  Serial.print(F("\n#SEND,"));
  Serial.println(command);
  BTSerial.print(command); // HM-10 commands have no CR/LF
  forwardFor(waitMs);
}

void startScan() {
  scanning = true;
  scanStartedAt = millis();
  markerIndex = 0;
  Serial.println(F("\n#SCAN_BEGIN"));
  BTSerial.print(F("AT+DISI?"));
}

void finishScan(const __FlashStringHelper* status) {
  scanning = false;
  Serial.print(F("\n#SCAN_"));
  Serial.println(status);
  nextScanAt = millis() + RESCAN_DELAY_MS;
}

void observeEnd(char value) {
  if (value == SCAN_END[markerIndex]) {
    markerIndex++;
    if (markerIndex == sizeof(SCAN_END) - 1) {
      markerIndex = 0;
      finishScan(F("END"));
    }
  } else {
    markerIndex = value == SCAN_END[0] ? 1 : 0;
  }
}

void setup() {
  Serial.begin(9600);
  BTSerial.begin(9600);
  delay(1200);

  Serial.println(F("#HM10_IBEACON_SCANNER,V1"));
  // AT must be followed quickly by IMME1 when central auto-work mode is active.
  sendCommand(F("AT"), 50);
  sendCommand(F("AT+IMME1"), 600);
  sendCommand(F("AT+ROLE1"), 600);
  sendCommand(F("AT+RESET"), 3000);
  nextScanAt = millis() + 500;
}

void loop() {
  while (BTSerial.available()) {
    const char value = (char)BTSerial.read();
    Serial.write(value);
    if (scanning) observeEnd(value);
  }

  const unsigned long now = millis();
  if (scanning && now - scanStartedAt >= SCAN_TIMEOUT_MS) finishScan(F("TIMEOUT"));
  if (!scanning && (long)(now - nextScanAt) >= 0) startScan();
}
