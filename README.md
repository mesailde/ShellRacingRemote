
![My Image](images/IMG_20230806_221812.jpg)

# README
With that code you will be able to control ShellRacing cars from ESP32. Currently
the direction is controlled by pulling the defined pins to ground, but using ESP32
you can put sensors like accelerometers,gyros,joysticks whatever you like
## What works?
```
Controlling the car in all directions including combinations like up+left
Robust connection and scanning
Turbo button is working
Lights also if supported by car
Battery percentage is printed every 60sec in the UART
```

## How to compile?
```
You can use platformio or Arduino IDE(make sure you don't have third party BLE libraries)
```

## Links
```
https://gist.github.com/scrool/e79d6a4cb50c26499746f4fe473b3768
https://github.com/tmk907/RacingCarsController/tree/master
```
