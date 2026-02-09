//Demo file for 2 servo arms to press buttons - flash this to an Arduino
// Protocol (line oriented, no delays):
//   "P0" -> press left   (index 0)
//   "P1" -> press right  (index 1)
//   "PA" -> press both simultaneously
//
// Prints: "READY", then "OK P0"/"OK P1"/"OK PA" or an error line.

#include <Arduino.h> 
#include <Servo.h> //in Arduino IDE: Tools -> Manage Libraries... -> search "Servo" -> install

const uint8_t SERVO_PINS[2] = {9, 10};  // left, right
// Rest angles (not pressing) and press angles
int ANGLE_FROM[2] = {20, 5}; //PLAY WITH THESE VALUES
int ANGLE_TO  [2] = {50, 100}; //DEPENDING ON THE BUTTON SENS     

// Timing of one press
uint16_t PRESS_TRAVEL_MS = 120;   // time to get down
uint16_t PRESS_HOLD_MS   = 30;   // time to keep contact
uint16_t PRESS_SETTLE_MS = 80;   // time before accepting new press (+ move up)


enum Phase : uint8_t { IDLE, DOWN, HOLD, UP, SETTLE };

struct PressSM {
  Servo   sv;
  int     angFrom;
  int     angTo;
  Phase   phase = IDLE;
  uint32_t t0   = 0;        // phase start time
};

// state machines (left=0, right=1)
PressSM M[2];

void startPress(uint8_t i) {
  if (i > 1) return;
  // If already active, ignore
  if (M[i].phase != IDLE) return;

  M[i].sv.write(M[i].angTo);   // go down (non-blocking)
  M[i].phase = DOWN;
  M[i].t0    = millis();
}

void updatePressSM(uint8_t i) {
  PressSM &m = M[i];
  uint32_t now = millis();

  switch (m.phase) {
    case IDLE: break;

    case DOWN:
      if (now - m.t0 >= PRESS_TRAVEL_MS) {
        m.phase = HOLD;
        m.t0    = now;
        // already at angTo, wait the hold time
      }
      break;

    case HOLD:
      if (now - m.t0 >= PRESS_HOLD_MS) {
        m.sv.write(m.angFrom);       // go up
        m.phase = UP;
        m.t0    = now;
      }
      break;

    case UP:
      if (now - m.t0 >= PRESS_TRAVEL_MS) {
        m.phase = SETTLE;
        m.t0    = now;
      }
      break;

    case SETTLE:
      if (now - m.t0 >= PRESS_SETTLE_MS) {
        m.phase = IDLE;
      }
      break;
  }
}

void press_both() {
  startPress(0);
  startPress(1);
}

void press_button(uint8_t idx) {
  startPress(idx);
}

void setup() {
  Serial.begin(115200);
  // Attach and move to rest angles
  M[0].sv.attach(SERVO_PINS[0]); M[0].angFrom = ANGLE_FROM[0]; M[0].angTo = ANGLE_TO[0]; M[0].sv.write(M[0].angFrom);
  M[1].sv.attach(SERVO_PINS[1]); M[1].angFrom = ANGLE_FROM[1]; M[1].angTo = ANGLE_TO[1]; M[1].sv.write(M[1].angFrom);

  // Give power/USB a moment to stabilize
  delay(300);
  Serial.println(F("READY"));
}

void loop() {
  // Update both state machines every loop - no delay()!
  updatePressSM(0);
  updatePressSM(1);

  // Non-blocking serial command parser
  if (Serial.available()) {
    String cmd = Serial.readStringUntil('\n');
    cmd.trim(); cmd.toUpperCase();

    if (cmd == "P0")          { press_button(0); Serial.println(F("OK P0")); }
    else if (cmd == "P1")     { press_button(1); Serial.println(F("OK P1")); }
    else if (cmd == "PA")     { press_both();    Serial.println(F("OK PA")); }
    else if (cmd.length())    { Serial.println(F("ERR (use P0, P1, or PA)")); }
  }
}