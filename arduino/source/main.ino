#include <Servo.h>
#include <SPI.h>
#include <string.h>

// Ultrasonic
const int ECHO_PIN = 7;
const int TRIG_PIN = 6;
const unsigned long ECHO_TIMEOUT_US = 30000;
const int DISTANCE_THRESHOLD_CM = 30;

// Stepper Motor 28BYJ-48
const int IN1 = 5;
const int IN2 = 4;
const int IN3 = 3;
const int IN4 = 2;

// Relay Module for DC motor
const int IMD1 = A2;
const int IMD2 = A3;
const int PIN_Driver = 8;
const int controlSpeed = 127; // 0-255

// RFID RC522 pins
const int PIN_RST = 9;
const int PIN_SDA = 10;

// --- RC522 Register Map ---
#define REG_RF_CFG    0x26
#define REG_COMMAND        0x01
#define REG_COM_I_EN       0x02
#define REG_COM_IRQ        0x04
#define REG_ERROR          0x06
#define REG_FIFO_DATA      0x09
#define REG_FIFO_LEVEL     0x0A
#define REG_CONTROL        0x0C
#define REG_BIT_FRAMING    0x0D
#define REG_COLL           0x0E
#define REG_MODE           0x11
#define REG_TX_CONTROL     0x14
#define REG_TX_ASK         0x15
#define REG_CRC_RESULT_H   0x21
#define REG_CRC_RESULT_L   0x22
#define REG_MOD_WIDTH      0x24
#define REG_T_MODE         0x2A
#define REG_T_PRESCALER    0x2B
#define REG_T_RELOAD_H     0x2C
#define REG_T_RELOAD_L     0x2D
#define REG_VERSION        0x37

// --- RC522 Commands ---
#define CMD_IDLE           0x00
#define CMD_TRANSCEIVE     0x0C
#define CMD_CALC_CRC       0x03
#define CMD_SOFT_RESET     0x0F

// --- PICC Commands ---
#define PICC_REQA          0x26
#define PICC_ANTICOLL      0x93
#define PICC_SEL_CL1       0x93
#define PICC_HALT_A        0x50

Servo servo1;
Servo servo2;

unsigned long lastStepTime = 0;
int currentStep = 0;

// Motor state
bool motorEnabled = true;
int stepDelay = 7000;
bool usedms = true;
int stepDelayms = 2;

const int stepSequence[8][4] = {{1,0,0,0},{1,1,0,0},{0,1,0,0},{0,1,1,0},{0,0,1,0},{0,0,1,1},{0,0,0,1},{1,0,0,1}};

// ==================== VARIABLES FOR 3‑SECOND DETECTION ====================
bool doorOpen = false;
bool objectPresent = false;
unsigned long detectionStartTime = 0;
unsigned long noDetectionStartTime = 0;

// Servo positions
const int SERVO1_CLOSED = 180 - 2;
const int SERVO2_CLOSED = 2;
const int SERVO1_OPEN   = 180 - 56;
const int SERVO2_OPEN   = 56;

// ==================== AI BUSY FLAG ====================
bool aiIsBusy = false;
bool StartAI = false;

// ==================== MAINTENANCE MODE ====================
const uint8_t MAINTENANCE_UID[4] = {0xDC, 0x6D, 0x66, 0x06};
bool maintenanceMode = false;
unsigned long lastMaintenanceTap = 0;
const unsigned long MAINTENANCE_COOLDOWN_MS = 3000;

void setup() {
  Serial.begin(9600);
  while(!Serial);
  SPI.begin();
  pinMode(PIN_RST, OUTPUT);
  pinMode(PIN_SDA, OUTPUT);
  digitalWrite(PIN_SDA, HIGH);
  rc522_init();

  pinMode(TRIG_PIN, OUTPUT);
  pinMode(ECHO_PIN, INPUT);
  digitalWrite(TRIG_PIN, LOW);

  pinMode(IN1, OUTPUT);
  pinMode(IN2, OUTPUT);
  pinMode(IN3, OUTPUT);
  pinMode(IN4, OUTPUT);
  pinMode(PIN_Driver, OUTPUT);
  pinMode(IMD1, OUTPUT);
  pinMode(IMD2, OUTPUT);

  digitalWrite(PIN_Driver, controlSpeed);
  digitalWrite(IMD1, LOW);
  digitalWrite(IMD2, LOW);

  doorOpen = false;
  releaseMotor();
}

void loop() {
  if (Serial.available() > 0) {
    char cmd = Serial.read();
    if (cmd == 'B') { aiIsBusy = true; }
    else if (cmd == 'I') { aiIsBusy = false; }
    else if (cmd == 'O') { openDoor(); }
    else if (cmd == 'C') { closeDoor(); }
    else if (cmd == 'CVD') {
      Serial.print("<Echo>"); Serial.print(ECHO_PIN); Serial.println("</Echo>");
      Serial.print("<Trig>"); Serial.print(TRIG_PIN); Serial.println("</Trig>");
      Serial.print("<Door_Boolen>"); Serial.print(doorOpen); Serial.println("</Door_Boolen>");
      Serial.print("<Object_Boolen>"); Serial.print(objectPresent); Serial.println("</Object_Boolen>");
      Serial.print("<Maintenance_Boolen>"); Serial.print(maintenanceMode); Serial.println("</Maintenance_Boolen>");
      Serial.print("<Ai_Boolen>"); Serial.print(aiIsBusy); Serial.println("</Ai_Boolen>");
    } 
    else if (cmd == '<') {
      String longCmd = Serial.readStringUntil('>');
      if (longCmd == "START") StartAI = true;
      else if (longCmd == "END") StartAI = false;
    }
  }
  uint8_t atqa[2] = {0};
  uint8_t uid[4]  = {0};
  if (rc522_request(atqa)) {
    if (rc522_anticoll(uid)) {
      Serial.print("Card UID: ");
      for (uint8_t i = 0; i < 4; i++) {
        Serial.print(uid[i], HEX);
        Serial.print(" ");
      }
      Serial.println();
      if (memcmp(uid, MAINTENANCE_UID, 4) == 0) {
        if (millis() - lastMaintenanceTap >= MAINTENANCE_COOLDOWN_MS) {
          lastMaintenanceTap = millis();
          if (!maintenanceMode) {
            maintenanceMode = true;
            Serial.println("<MAINTENANCE-ENTER>");
            if (!doorOpen) {
              openDoor();
            } else {}
          } else {
            maintenanceMode = false;
            Serial.println("<MAINTENANCE-EXIT>");
            if (doorOpen) {
              closeDoor();
            }
            detectionStartTime = 0;
            noDetectionStartTime = 0;
            objectPresent = false;
          }
        }
      }
    }
    rc522_halt();
  }

  if (StartAI && !maintenanceMode) {
    long distance = ultrasonicDistanceCm();
    bool nowPresent = (distance > 0 && distance < DISTANCE_THRESHOLD_CM);

    if (nowPresent && !objectPresent) {
      detectionStartTime = millis();
      objectPresent = true;
    } else if (!nowPresent && objectPresent) {
      noDetectionStartTime = millis();
      objectPresent = false;
    }

    if (objectPresent && !doorOpen) {
      if (millis() - detectionStartTime >= 3000) {
        openDoor();
      }
    } 
    
    if (!objectPresent && doorOpen && !aiIsBusy) {
      if (noDetectionStartTime != 0 && (millis() - noDetectionStartTime >= 3000)) {
        closeDoor();
      }
    }

    if (aiIsBusy) {
      noDetectionStartTime = millis();
    }
  }
  
  delay(50); 
}

void openDoor() {
  if (!doorOpen) {
    slowServoMove(SERVO1_CLOSED, SERVO1_OPEN, SERVO2_CLOSED, SERVO2_OPEN, 15);
    doorOpen = true;
    delay(1000);
    digitalWrite(IMD1, HIGH);
    digitalWrite(IMD2, LOW);
    rotateSteps(900, 1);
    digitalWrite(IMD1, LOW); digitalWrite(IMD2, LOW);
    delay(2000);
    Serial.print("<FUNCTION-001>");
    Serial.println(" ");
  }
}

void closeDoor() {
  if (doorOpen) {
    digitalWrite(IMD1, LOW);
    digitalWrite(IMD2, HIGH);
    rotateSteps(900, -1);
    digitalWrite(IMD1, LOW); digitalWrite(IMD2, LOW);
    delay(2000);
    slowServoMove(SERVO1_OPEN, SERVO1_CLOSED, SERVO2_OPEN, SERVO2_CLOSED, 30);
    delay(1000);
    doorOpen = false;
    Serial.print("<FUNCTION-000>");
    Serial.println(" ");
  }
}

// ----------------------------------------------------------------------
// Move both servos slowly at the same time
// ----------------------------------------------------------------------
void slowServoMove(int start1, int end1, int start2, int end2, int speedDelayMs) {
  servo1.attach(A0); servo2.attach(A1);
  int maxDiff = max(abs(end1 - start1), abs(end2 - start2));
  if (maxDiff == 0) return;
  for (int i = 0; i <= maxDiff; i++) {
    int pos1 = map(i, 0, maxDiff, start1, end1);
    int pos2 = map(i, 0, maxDiff, start2, end2);
    servo1.write(pos1);
    servo2.write(pos2);
    delay(speedDelayMs);
  }
  servo1.detach(); servo2.detach();
}

// ----------------------------------------------------------------------
// Ultrasonic distance reading
// ----------------------------------------------------------------------
long ultrasonicDistanceCm() {
  digitalWrite(TRIG_PIN, HIGH);
  delayMicroseconds(10);
  digitalWrite(TRIG_PIN, LOW);
  unsigned long duration = pulseIn(ECHO_PIN, HIGH, ECHO_TIMEOUT_US);
  if (duration == 0) return 0;
  return duration / 58;
}

// ----------------------------------------------------------------------
// Stepper Motor Functions
// ----------------------------------------------------------------------
void rotateSteps(int steps, int direction) {
  for (int i = 0; i < steps; i++) {
    currentStep += direction;
    if (currentStep >= 8) currentStep = 0;
    if (currentStep < 0) currentStep = 7;
    setMotorPins(stepSequence[currentStep][0],stepSequence[currentStep][1],stepSequence[currentStep][2],stepSequence[currentStep][3]);
    if (usedms) delay(stepDelayms);
    else delayMicroseconds(stepDelay);
  }
  releaseMotor();
}

void setMotorPins(int a, int b, int c, int d) {
  digitalWrite(IN1, a);digitalWrite(IN2, b);digitalWrite(IN3, c);digitalWrite(IN4, d);
}

void releaseMotor() {
  digitalWrite(IN1, LOW);digitalWrite(IN2, LOW);digitalWrite(IN3, LOW);digitalWrite(IN4, LOW);
}

// ============================================================
//  RC522 Low‑Level & Card Communication (unchanged)
// ============================================================
void rc522_write(uint8_t reg, uint8_t val) {
  SPI.beginTransaction(SPISettings(4000000, MSBFIRST, SPI_MODE0));
  digitalWrite(PIN_SDA, LOW);
  SPI.transfer((reg << 1) & 0x7E);
  SPI.transfer(val);
  digitalWrite(PIN_SDA, HIGH);
  SPI.endTransaction();
}

uint8_t rc522_read(uint8_t reg) {
  SPI.beginTransaction(SPISettings(4000000, MSBFIRST, SPI_MODE0));
  digitalWrite(PIN_SDA, LOW);
  SPI.transfer(((reg << 1) & 0x7E) | 0x80);
  uint8_t val = SPI.transfer(0x00);
  digitalWrite(PIN_SDA, HIGH);
  SPI.endTransaction();
  return val;
}

void rc522_set_bits(uint8_t reg, uint8_t mask) {rc522_write(reg, rc522_read(reg) | mask);}
void rc522_clear_bits(uint8_t reg, uint8_t mask) {rc522_write(reg, rc522_read(reg) & (~mask));}

void rc522_reset() {
  rc522_write(REG_COMMAND, CMD_SOFT_RESET);
  delay(50);
}

void rc522_init() {
  digitalWrite(PIN_RST, LOW);
  delayMicroseconds(100);
  digitalWrite(PIN_RST, HIGH);
  delay(50);
  rc522_reset();
  rc522_write(REG_T_MODE,      0x80);
  rc522_write(REG_T_PRESCALER, 0xA9);
  rc522_write(REG_T_RELOAD_H,  0x03);
  rc522_write(REG_T_RELOAD_L,  0xE8);
  rc522_write(REG_TX_ASK,      0x40);
  rc522_write(REG_MODE,        0x3D);
  rc522_write(REG_RF_CFG,      0x38);   // 48dB gain
  rc522_set_bits(REG_TX_CONTROL, 0x03);
  Serial.print("RC522 version: 0x");
  Serial.println(rc522_read(REG_VERSION), HEX);
}

bool rc522_calc_crc(uint8_t *data, uint8_t len, uint8_t *out) {
  rc522_write(REG_COMMAND, CMD_IDLE);
  rc522_clear_bits(REG_COM_IRQ, 0x80);
  rc522_set_bits(REG_FIFO_LEVEL, 0x80);
  for (uint8_t i = 0; i < len; i++)
    rc522_write(REG_FIFO_DATA, data[i]);
  rc522_write(REG_COMMAND, CMD_CALC_CRC);
  uint16_t timeout = 5000;
  while (--timeout) {
    if (rc522_read(REG_COM_IRQ) & 0x04) break;
  }
  if (timeout == 0) return false;
  out[0] = rc522_read(REG_CRC_RESULT_L);
  out[1] = rc522_read(REG_CRC_RESULT_H);
  return true;
}

struct TransceiveResult {
  uint8_t data[16];
  uint8_t len;
  bool    ok;
};

TransceiveResult rc522_transceive(uint8_t *send, uint8_t sendLen, uint8_t lastBits = 0) {
  TransceiveResult res = {0};
  rc522_write(REG_COMMAND, CMD_IDLE);
  rc522_write(REG_COM_IRQ, 0x7F);
  rc522_set_bits(REG_FIFO_LEVEL, 0x80);
  rc522_write(REG_COM_I_EN, 0x77);
  for (uint8_t i = 0; i < sendLen; i++)
    rc522_write(REG_FIFO_DATA, send[i]);
  rc522_write(REG_BIT_FRAMING, lastBits & 0x07);
  rc522_write(REG_COMMAND, CMD_TRANSCEIVE);
  rc522_set_bits(REG_BIT_FRAMING, 0x80);
  uint16_t timeout = 2000;
  uint8_t irq;
  do {
    irq = rc522_read(REG_COM_IRQ);
    timeout--;
  } while (timeout && !(irq & 0x31));
  rc522_clear_bits(REG_BIT_FRAMING, 0x80);
  if (timeout == 0) return res;
  if (irq & 0x01)   return res;
  uint8_t err = rc522_read(REG_ERROR);
  if (err & 0x13) return res;
  uint8_t rxBytes = rc522_read(REG_FIFO_LEVEL);
  if (rxBytes > sizeof(res.data)) rxBytes = sizeof(res.data);
  for (uint8_t i = 0; i < rxBytes; i++)
    res.data[i] = rc522_read(REG_FIFO_DATA);
  res.len = rxBytes;
  res.ok  = true;
  return res;
}

bool rc522_request(uint8_t *atqa) {
  rc522_write(REG_BIT_FRAMING, 0x07);
  rc522_clear_bits(REG_COLL, 0x80);
  uint8_t cmd = PICC_REQA;
  TransceiveResult res = rc522_transceive(&cmd, 1, 7);
  if (!res.ok || res.len != 2) return false;
  atqa[0] = res.data[0];
  atqa[1] = res.data[1];
  return true;
}

bool rc522_anticoll(uint8_t *uid) {
  uint8_t cmd[2] = { PICC_ANTICOLL, 0x20 };
  rc522_write(REG_BIT_FRAMING, 0x00);
  rc522_clear_bits(REG_COLL, 0x80);
  TransceiveResult res = rc522_transceive(cmd, 2);
  if (!res.ok || res.len != 5) return false;
  uint8_t bcc = res.data[0] ^ res.data[1] ^ res.data[2] ^ res.data[3];
  if (bcc != res.data[4]) return false;
  for (uint8_t i = 0; i < 4; i++)
    uid[i] = res.data[i];
  return true;
}

void rc522_halt() {
  uint8_t buf[4];
  buf[0] = PICC_HALT_A;
  buf[1] = 0x00;
  rc522_calc_crc(buf, 2, &buf[2]);
  rc522_transceive(buf, 4);
}