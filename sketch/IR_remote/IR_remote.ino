#define RAW_BUFFER_LENGTH 750
#include <IRremote.hpp>
#include <stdlib.h>
#include <string.h>

// Forward declarations
void printHelp();
void handleSerialLine(char *line);
bool parseAndSendPronto(char *payload, const char *&errorText);
void runLEDHardwareTest();
bool printLastCapturedPronto();
void printHexWord(uint16_t value);

#define IR_RECEIVE_PIN 2
#define IR_SEND_PIN 3
#define LED_FEEDBACK_PIN 4

// LED states
#define LED_STATE_IDLE 0
#define LED_STATE_FLASH_ONCE 1
#define LED_STATE_FAST_FLASH 2
#define LED_STATE_SOLID 3
#define LED_STATE_SLOW_BLINK 4

const uint16_t TEST_NEC_ADDRESS = 0x00;
const uint8_t TEST_NEC_COMMAND = 0x34;
const uint8_t TEST_NEC_REPEATS = 0;

const uint16_t MAX_RAW_LEN = 300;
const uint16_t MAX_SERIAL_LINE = 1400;
const uint32_t LEARN_CAPTURE_WINDOW_MS = 5000;
const uint32_t LEARN_SETTLE_MS = 180;
const uint32_t LEARN_APPEND_GAP_MS = 260;

char serialLine[MAX_SERIAL_LINE];
uint16_t serialLineLen = 0;

uint16_t rawMicros[MAX_RAW_LEN];
uint16_t rawLen = 0;

decode_type_t lastProtocol = UNKNOWN;
uint16_t lastAddress = 0;
uint16_t lastCommand = 0;
uint8_t lastBits = 0;
bool haveDecoded = false;
bool haveRaw = false;
uint32_t lastRawCaptureMillis = 0;
bool learnSessionActive = false;
uint32_t learnSessionStartMillis = 0;
uint32_t learnFirstCaptureMillis = 0;

// LED feedback variables
uint8_t ledState = LED_STATE_IDLE;
uint8_t ledNextState = LED_STATE_IDLE;
uint32_t ledStateTime = 0;
bool ledIsOn = false;
uint32_t ledFlashCount = 0;

void setLEDState(uint8_t newState) {
  if (newState != ledState) {
    ledState = newState;
    ledStateTime = millis();
    ledFlashCount = 0;
  }
}

void updateLED() {
  uint32_t elapsed = millis() - ledStateTime;
  
  switch (ledState) {
    case LED_STATE_IDLE:
      digitalWrite(LED_FEEDBACK_PIN, LOW);
      ledIsOn = false;
      break;
      
    case LED_STATE_FLASH_ONCE:
      // Flash for 100ms, then return to idle
      if (elapsed < 100) {
        digitalWrite(LED_FEEDBACK_PIN, HIGH);
        ledIsOn = true;
      } else {
        digitalWrite(LED_FEEDBACK_PIN, LOW);
        ledIsOn = false;
        setLEDState(LED_STATE_IDLE);
      }
      break;
      
    case LED_STATE_FAST_FLASH:
      // 100ms on, 100ms off pattern (200ms total)
      if ((elapsed / 100) % 2 == 0) {
        digitalWrite(LED_FEEDBACK_PIN, HIGH);
        ledIsOn = true;
      } else {
        digitalWrite(LED_FEEDBACK_PIN, LOW);
        ledIsOn = false;
      }
      break;
      
    case LED_STATE_SOLID:
      digitalWrite(LED_FEEDBACK_PIN, HIGH);
      ledIsOn = true;
      break;
      
    case LED_STATE_SLOW_BLINK:
      // 500ms on, 500ms off pattern (1000ms total)
      if ((elapsed / 500) % 2 == 0) {
        digitalWrite(LED_FEEDBACK_PIN, HIGH);
        ledIsOn = true;
      } else {
        digitalWrite(LED_FEEDBACK_PIN, LOW);
        ledIsOn = false;
      }
      break;
  }
}

void runLEDHardwareTest() {
  Serial.print(F("LED test on pin "));
  Serial.println(LED_FEEDBACK_PIN);
  Serial.println(F("Forcing 6 blinks: HIGH 200ms / LOW 200ms"));

  // Pause state machine and directly drive the pin for wiring/polarity checks.
  setLEDState(LED_STATE_IDLE);
  for (uint8_t i = 0; i < 6; i++) {
    digitalWrite(LED_FEEDBACK_PIN, HIGH);
    delay(200);
    digitalWrite(LED_FEEDBACK_PIN, LOW);
    delay(200);
  }

  Serial.println(F("LED test complete."));
}

void printHexWord(uint16_t value) {
  if (value < 0x1000) Serial.print('0');
  if (value < 0x100) Serial.print('0');
  if (value < 0x10) Serial.print('0');
  Serial.print(value, HEX);
}

bool printLastCapturedPronto() {
  if (!haveRaw || rawLen < 2) {
    Serial.println(F("ERR no recent capture"));
    return false;
  }

  if (learnSessionActive && (uint32_t)(millis() - lastRawCaptureMillis) < LEARN_SETTLE_MS) {
    Serial.println(F("ERR capture settling"));
    return false;
  }

  if ((uint32_t)(millis() - lastRawCaptureMillis) > LEARN_CAPTURE_WINDOW_MS) {
    Serial.println(F("ERR no recent capture"));
    return false;
  }

  uint16_t timingCount = (rawLen & 1U) ? (rawLen - 1U) : rawLen;
  if (timingCount < 2) {
    Serial.println(F("ERR captured code too short"));
    return false;
  }

  const uint16_t prontoFreqWord = 109;  // ~38 kHz carrier
  const float prontoUnitMicros = prontoFreqWord * 0.241246f;
  uint16_t burstPairs = timingCount / 2U;

  Serial.print(F("PRONTO 0000 "));
  printHexWord(prontoFreqWord);
  Serial.print(' ');
  printHexWord(burstPairs);
  Serial.print(F(" 0000"));

  for (uint16_t i = 0; i < timingCount; i++) {
    float prontoWordFloat = rawMicros[i] / prontoUnitMicros;
    if (prontoWordFloat < 1.0f) {
      prontoWordFloat = 1.0f;
    }
    if (prontoWordFloat > 65535.0f) {
      prontoWordFloat = 65535.0f;
    }
    uint16_t prontoWord = (uint16_t)(prontoWordFloat + 0.5f);
    Serial.print(' ');
    printHexWord(prontoWord);
  }

  Serial.println();
  return true;
}

bool parseAndSendPronto(char *payload, const char *&errorText) {
  uint16_t words[340];
  uint16_t wordCount = 0;

  char *token = strtok(payload, " \t");
  while (token != nullptr) {
    if (wordCount >= 340) {
      errorText = "ERR too many words";
      return false;
    }

    char *endPtr = nullptr;
    unsigned long value = strtoul(token, &endPtr, 16);
    if (endPtr == token || *endPtr != '\0' || value > 0xFFFFUL) {
      errorText = "ERR invalid hex word";
      return false;
    }

    words[wordCount++] = (uint16_t)value;
    token = strtok(nullptr, " \t");
  }

  if (wordCount < 6) {
    errorText = "ERR pronto too short";
    return false;
  }
  if (words[0] != 0x0000) {
    errorText = "ERR unsupported pronto type";
    return false;
  }
  if (words[1] == 0) {
    errorText = "ERR invalid frequency";
    return false;
  }

  uint16_t burstPairs = words[2] + words[3];
  uint16_t timingCount = burstPairs * 2;
  if ((uint16_t)(4 + timingCount) > wordCount) {
    errorText = "ERR pronto length mismatch";
    return false;
  }
  if (timingCount == 0 || timingCount > MAX_RAW_LEN) {
    errorText = "ERR timing count out of range";
    return false;
  }

  float prontoUnitMicros = words[1] * 0.241246f;
  float carrierKhzFloat = 1000.0f / prontoUnitMicros;
  uint8_t carrierKhz = (uint8_t)(carrierKhzFloat + 0.5f);
  if (carrierKhz < 20 || carrierKhz > 60) {
    errorText = "ERR carrier out of range";
    return false;
  }

  for (uint16_t i = 0; i < timingCount; i++) {
    float micros = words[4 + i] * prontoUnitMicros;
    if (micros < 1.0f) {
      micros = 1.0f;
    }
    if (micros > 65535.0f) {
      micros = 65535.0f;
    }
    rawMicros[i] = (uint16_t)(micros + 0.5f);
  }

  IrSender.sendRaw(rawMicros, timingCount, carrierKhz);
  Serial.print(F("OK sent pronto, timings="));
  Serial.print(timingCount);
  Serial.print(F(", khz="));
  Serial.println(carrierKhz);
  setLEDState(LED_STATE_FLASH_ONCE);
  return true;
}

void handleSerialLine(char *line) {
  while (*line == ' ' || *line == '\t') {
    line++;
  }
  if (*line == '\0') {
    return;
  }

  char *endTrim = line + strlen(line) - 1;
  while (endTrim >= line && (*endTrim == ' ' || *endTrim == '\t')) {
    *endTrim-- = '\0';
  }

  if ((line[0] == 'P' || line[0] == 'p') && line[1] == ' ') {
    const char *errorText = nullptr;
    if (!parseAndSendPronto(line + 2, errorText) && errorText != nullptr) {
      Serial.println(errorText);
    }
    return;
  }

  if (strlen(line) == 1) {
    char c = line[0];

    if (c == 'r' || c == 'R') {
      if (haveDecoded && lastProtocol != UNKNOWN) {
        Serial.println(F("Re-sending decoded protocol..."));
        IrSender.write(lastProtocol, lastAddress, lastCommand, 0);
        setLEDState(LED_STATE_FLASH_ONCE);
      } else if (haveRaw) {
        Serial.print(F("Re-sending RAW ("));
        Serial.print(rawLen);
        Serial.println(F(" entries)..."));
        IrSender.sendRaw(rawMicros, rawLen, 38);
        setLEDState(LED_STATE_FLASH_ONCE);
      } else {
        Serial.println(F("No IR code captured yet."));
      }
      return;
    }

    if (c == 'n' || c == 'N') {
      Serial.print(F("Sending NEC test code: address=0x"));
      Serial.print(TEST_NEC_ADDRESS, HEX);
      Serial.print(F(", command=0x"));
      Serial.println(TEST_NEC_COMMAND, HEX);
      IrSender.sendNEC(TEST_NEC_ADDRESS, TEST_NEC_COMMAND, TEST_NEC_REPEATS);
      setLEDState(LED_STATE_FLASH_ONCE);
      return;
    }

    if (c == 'h' || c == 'H') {
      printHelp();
      return;
    }

    if (c == 'g' || c == 'G') {
      bool printed = printLastCapturedPronto();
      if (printed && learnSessionActive) {
        // Learned code has been consumed for this session.
        learnSessionActive = false;
      }
      return;
    }

    if (c == 'l' || c == 'L') {
      haveRaw = false;
      haveDecoded = false;
      rawLen = 0;
      lastRawCaptureMillis = 0;
      learnSessionActive = true;
      learnSessionStartMillis = millis();
      learnFirstCaptureMillis = 0;
      Serial.println(F("OK learn armed"));
      return;
    }

    if (c == 'c' || c == 'C') {
      haveRaw = false;
      haveDecoded = false;
      rawLen = 0;
      lastRawCaptureMillis = 0;
      learnSessionActive = false;
      learnFirstCaptureMillis = 0;
      Serial.println(F("OK cleared"));
      return;
    }

    if (c == 't' || c == 'T') {
      runLEDHardwareTest();
      return;
    }
  }

  Serial.println(F("ERR unknown command"));
}

void printHelp() {
  Serial.println(F("\nCommands:"));
  Serial.println(F("  r = resend last captured IR code"));
  Serial.println(F("  n = send fixed NEC test code"));
  Serial.println(F("  p <pronto hex> = send Pronto HEX from serial"));
  Serial.println(F("  g = print last captured code as Pronto HEX"));
  Serial.println(F("  l = arm learn session (capture only new IR)"));
  Serial.println(F("  c = clear last captured code"));
  Serial.println(F("  t = run LED hardware blink test"));
  Serial.println(F("  h = show this help"));
  Serial.println(F("\nLED States:"));
  Serial.println(F("  Off           = Idle"));
  Serial.println(F("  Flash once    = IR command sent"));
  Serial.println(F("  Fast flash    = Learning mode active"));
  Serial.println(F("  Solid on      = Receiving IR signal"));
  Serial.println(F("  Slow blink    = USB connected / ready"));
}

void setup() {
  Serial.begin(115200);
  while (!Serial) {
    ;
  }

  pinMode(LED_FEEDBACK_PIN, OUTPUT);
  setLEDState(LED_STATE_IDLE);

  IrReceiver.begin(IR_RECEIVE_PIN, ENABLE_LED_FEEDBACK);
  IrSender.begin(IR_SEND_PIN, DISABLE_LED_FEEDBACK);

  Serial.println(F("IR Learn + Replay ready."));
  Serial.println(F("Point remote at receiver and press a button..."));
  printHelp();
}

void loop() {
  updateLED();
  
  while (Serial.available()) {
    char ch = (char)Serial.read();
    if (ch == '\r') {
      continue;
    }
    if (ch == '\n') {
      serialLine[serialLineLen] = '\0';
      handleSerialLine(serialLine);
      serialLineLen = 0;
      continue;
    }

    if (serialLineLen < (MAX_SERIAL_LINE - 1)) {
      serialLine[serialLineLen++] = ch;
    } else {
      serialLineLen = 0;
      Serial.println(F("ERR serial line too long"));
    }
  }

  if (IrReceiver.decode()) {
    auto &d = IrReceiver.decodedIRData;

    setLEDState(LED_STATE_SOLID);
    updateLED();

    Serial.println(F("\n--- IR received ---"));
    IrReceiver.printIRResultShort(&Serial);
    IrReceiver.printIRSendUsage(&Serial);

    lastProtocol = d.protocol;
    lastAddress = d.address;
    lastCommand = d.command;
    lastBits = d.numberOfBits;
    haveDecoded = (d.protocol != UNKNOWN);

    uint16_t candidateLen = 0;
    if (IrReceiver.irparams.rawlen > 1) {
      uint16_t sourceLen = IrReceiver.irparams.rawlen;
      if (sourceLen > MAX_RAW_LEN) {
        sourceLen = MAX_RAW_LEN;
      }
      candidateLen = sourceLen - 1;
    }

    if (candidateLen > 0) {
      uint32_t nowMs = millis();
      bool stored = false;

      if (learnSessionActive && haveRaw && learnFirstCaptureMillis > 0 &&
          (uint32_t)(nowMs - lastRawCaptureMillis) <= LEARN_APPEND_GAP_MS) {
        uint16_t interFrameGapMicros = IrReceiver.irparams.rawbuf[0] * MICROS_PER_TICK;
        uint16_t needed = candidateLen + ((interFrameGapMicros > 0) ? 1U : 0U);
        if ((uint16_t)(rawLen + needed) <= MAX_RAW_LEN) {
          if (interFrameGapMicros > 0) {
            rawMicros[rawLen++] = interFrameGapMicros;
          }
          for (uint16_t i = 0; i < candidateLen; i++) {
            rawMicros[rawLen++] = IrReceiver.irparams.rawbuf[i + 1] * MICROS_PER_TICK;
          }
          haveRaw = true;
          lastRawCaptureMillis = nowMs;
          stored = true;
        }
      }

      if (!stored) {
        bool shouldStore = !haveRaw || candidateLen >= rawLen;
        if (learnSessionActive && lastRawCaptureMillis >= learnSessionStartMillis) {
          // During an armed learn session, keep the most complete frame seen so far.
          shouldStore = !haveRaw || candidateLen > rawLen;
        }

        if (shouldStore) {
          rawLen = candidateLen;
          for (uint16_t i = 0; i < rawLen; i++) {
            rawMicros[i] = IrReceiver.irparams.rawbuf[i + 1] * MICROS_PER_TICK;
          }
          haveRaw = true;
          lastRawCaptureMillis = nowMs;
          if (learnSessionActive && learnFirstCaptureMillis == 0) {
            learnFirstCaptureMillis = nowMs;
          }
        }
      }
    }

    Serial.print(F("Saved. Protocol="));
    Serial.print(getProtocolString(lastProtocol));
    Serial.print(F(", Address=0x"));
    Serial.print(lastAddress, HEX);
    Serial.print(F(", Command=0x"));
    Serial.print(lastCommand, HEX);
    Serial.print(F(", Bits="));
    Serial.println(lastBits);

    Serial.println(F("Type 'r' in Serial Monitor to resend."));
    IrReceiver.resume();
    setLEDState(LED_STATE_IDLE);
  }
}