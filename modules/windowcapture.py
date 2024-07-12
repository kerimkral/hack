import numpy as np
import win32gui, win32ui, win32con,win32com.client
from threading import Thread, Lock
from ctypes import windll
import tkinter
from time import time
from settings import Settings
import cv2 as cv
from math import *

class WindowCapture:

    # threading properties
    stopped = True
    lock = None
    screenshot = None
    
    # properties
    w = 0
    h = 0
    hwnd = None
    cropped_x = 0
    cropped_y = 0
    offset_x = 0
    offset_y = 0
    fps = 0
    avg_fps = 0

    # input trapezium for the perspective transform 
    TL = [426/1860,159/1046]
    TR = [1434/1860, 159/1046]
    BR = [1521/1860, 1]
    BL = [338/1860, 1]



    # constructor
    def __init__(self, window_name=None):

        ## Find DPI
        # Make program aware of DPI scaling
        # https://stackoverflow.com/a/45911849
        user32 = windll.user32
        user32.SetProcessDPIAware()
        # get DPI
        root = tkinter.Tk()
        dpi = root.winfo_fpixels('1i')
        deafault_dpi = 96
        self.scaling = int(dpi/deafault_dpi)
        # close tkinter
        root.destroy()

        # create a thread lock object
        self.lock = Lock()
        
        # find the handle for the window we want to capture.
        # if no window name is given, capture the entire screen
        if window_name is None:
            self.hwnd = win32gui.GetDesktopWindow()
        else:
            self.hwnd = win32gui.FindWindow(None, window_name)
            if not self.hwnd:
                raise Exception(f"{window_name} not found. Please open {window_name} or change the window_name at settings.py")

        # get the window size
        window_rect = win32gui.GetWindowRect(self.hwnd)
        self.w = window_rect[2] - window_rect[0]
        self.h = window_rect[3] - window_rect[1]
        
        # top left and bottom right coordinate of the window
        self.left = window_rect[0]
        self.top = window_rect[1]
        self.right = window_rect[2]
        self.bottom = window_rect[3]
        
        # screen resolution
        self.screen_resolution = user32.GetSystemMetrics(0), user32.GetSystemMetrics(1)

        # account for the window border and titlebar and cut them off
        self.border_pixels = int(1*self.scaling)
        self.titlebar_pixels = int(33*self.scaling)
        self.w = self.w - (self.border_pixels * 2)
        self.h = self.h - self.titlebar_pixels - self.border_pixels
        self.cropped_x = self.border_pixels
        self.cropped_y = self.titlebar_pixels

        # set the cropped coordinates offset so we can translate screenshot
        # images into actual screen positions
        self.offset_x = window_rect[0] + self.cropped_x
        self.offset_y = window_rect[1] + self.cropped_y
        self.offsets = (self.offset_x,self.offset_y)

        if Settings.focused_window:
            self.window = self.hwnd
            self.cropped = (self.cropped_x,self.cropped_y)
        else:
            self.window = None
            self.cropped = (self.offset_x,self.offset_y)
        
        # tile scaling
        self.tilePerWidth = self.w/27.35
        self.tilePerHeight = self.h/16.97
        self.perspectiveMatrix = self.find_perspective()


    # translate a pixel position on a screenshot image to a pixel position on the screen.
    # pos = (x, y)
    # WARNING: if you move the window being captured after execution is started, this will
    # return incorrect coordinates, because the window position is only calculated in
    # the __init__ constructor.
    def get_screen_position(self, cordinate):
        """
        Apply bluestacks' window offset
        :param cordinate (tuple): cordinate in the cropped screenshot
        :return: cordinate with offset applied
        """
        return (cordinate[0] + self.offset_x, cordinate[1] + self.offset_y)
    
    def get_brawler_center(self):
        return (self.w//2,self.h//2 + int(self.h*Settings.midpointOffsetScale))

    # https://stackoverflow.com/a/15503675
    # Focus the selected window
    def focus_window(self):
        if self.hwnd:
            shell = win32com.client.Dispatch("WScript.Shell")
            shell.SendKeys('%')
            win32gui.SetForegroundWindow(self.hwnd)

    # Returns the dimension of the window
    def get_dimension(self):
        return self.w, self.h

    # Take a screenshot of the window
    def get_screenshot(self):
        # get the window image data
        wDC = win32gui.GetWindowDC(self.window)
        dcObj = win32ui.CreateDCFromHandle(wDC)
        cDC = dcObj.CreateCompatibleDC()
        dataBitMap = win32ui.CreateBitmap()
        dataBitMap.CreateCompatibleBitmap(dcObj, self.w, self.h)
        cDC.SelectObject(dataBitMap)
        cDC.BitBlt((0, 0), (self.w, self.h), dcObj, self.cropped, win32con.SRCCOPY)

        # convert the raw data into a format opencv can read
        #dataBitMap.SaveBitmapFile(cDC, 'debug.bmp')
        signedIntsArray = dataBitMap.GetBitmapBits(True)
        img = np.fromstring(signedIntsArray, dtype='uint8')
        img.shape = (self.h, self.w, 4)

        # free resources
        dcObj.DeleteDC()
        cDC.DeleteDC()
        win32gui.ReleaseDC(self.hwnd, wDC)
        win32gui.DeleteObject(dataBitMap.GetHandle())

        # drop the alpha channel, or cv.matchTemplate() will throw an error like:
        #   error: (-215:Assertion failed) (depth == CV_8U || depth == CV_32F) && type == _templ.type() 
        #   && _img.dims() <= 2 in function 'cv::matchTemplate'
        img = img[...,:3]

        # make image C_CONTIGUOUS to avoid errors that look like:
        #   File ... in draw_rectangles
        #   TypeError: an integer is required (got type tuple)
        # see the discussion here:
        # https://github.com/opencv/opencv/issues/14866#issuecomment-580207109
        img = np.ascontiguousarray(img)

        return img

    # find the name of the window you're interested in.
    # once you have it, update window_capture()
    # https://stackoverflow.com/questions/55547940/how-to-get-a-list-of-the-name-of-every-open-window
    @staticmethod
    def list_window_names():
        def winEnumHandler(hwnd, ctx):
            if win32gui.IsWindowVisible(hwnd):
                print(hex(hwnd), f"\"{win32gui.GetWindowText(hwnd)}\"")
        win32gui.EnumWindows(winEnumHandler, None)

    def find_perspective(self):
        inputCorners = np.float32([[self.TL[0]*self.w,self.TL[1]*self.h], 
                                   [self.TR[0]*self.w,self.TR[1]*self.h], 
                                   [self.BR[0]*self.w,self.BR[1]*self.h], 
                                   [self.BL[0]*self.w,self.BL[1]*self.h]])
        # Calculate the width and height of the new perspective
        outputWidth = round(hypot(inputCorners [0, 0] - inputCorners [1, 0], inputCorners [0, 1] - inputCorners [1, 1]))
        outputHeight = round(hypot(inputCorners [0, 0] - inputCorners [3, 0], inputCorners [0, 1] - inputCorners [3, 1]))
        
        # Set the upper left coordinates for the output rectangle
        x, y = inputCorners[0, 0], inputCorners[0, 1]

        # Specify output coordinates for corners of red quadrilateral in order TL, TR, BR, BL as x, y
        outputCorners = np.float32([[x, y], [x + outputWidth, y], [x + outputWidth, y + outputHeight], [x, y + outputHeight]])

        # Compute the perspective transformation matrix
        matrix = cv.getPerspectiveTransform(inputCorners, outputCorners)
        return matrix
    
    # Function to transform a coordinate from the input plane to the output plane
    def transform_coordinate(self,point):
        pointHomogeneous = np.array([point[0], point[1], 1.0])
        transformedPointHomogeneous = np.dot(self.perspectiveMatrix, pointHomogeneous)
        transformedPointHomogeneous /= transformedPointHomogeneous[2]
        return int(transformedPointHomogeneous[0]), int(transformedPointHomogeneous[1])
    
    # threading methods
    def start(self):
        self.stopped = False
        self.loop_time = time()
        self.count = 0
        t = Thread(target=self.run)
        t.setDaemon(True)
        t.start()

    def stop(self):
        self.stopped = True

    def run(self):
        while not self.stopped:
            # get an updated image of the game
            screenshot = self.get_screenshot()
            # lock the thread while updating the results
            self.lock.acquire()
            self.screenshot = screenshot
            self.lock.release()
            
            self.fps = (1 / (time() - self.loop_time))
            self.loop_time = time()
            self.count += 1
            if self.count == 1:
                self.avg_fps = self.fps
            else:
                self.avg_fps = (self.avg_fps*self.count+self.fps)/(self.count + 1)