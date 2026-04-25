#pragma once

namespace hexe::board::pins {

constexpr int kDisplayCs = 5;
constexpr int kDisplayDc = 4;
constexpr int kDisplayReset = 48;
constexpr int kDisplayClk = 7;
constexpr int kDisplayMosi = 6;
constexpr int kDisplayMiso = 13;

constexpr int kButtonTopLeft = 0;
constexpr int kBacklight = 47;
constexpr int kSpeakerEnable = 46;

constexpr int kI2cScl = 18;
constexpr int kI2cSda = 8;

constexpr int kI2sLrclk = 45;
constexpr int kI2sBclk = 17;
constexpr int kI2sMclk = 2;
constexpr int kI2sMicDin = 16;
constexpr int kI2sSpeakerDout = 15;

}  // namespace hexe::board::pins
