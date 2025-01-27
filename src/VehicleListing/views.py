from django.shortcuts import render
from .models import import_listing_from_url
from .gumtree_scraper import get_listings
import logging
from .url_importer import ImportFromUrl, ImportFromSourceOption
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from webdriver_manager.chrome import ChromeDriverManager
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.common.keys import Keys
import time
import random
import os

# Global variable to store the driver instance
global_driver = None

def import_url(request):
    if request.method == 'POST':
        url = request.POST.get('url')
        import_url = ImportFromUrl(url)
        is_valid, error_message = import_url.validate()

        if not is_valid:
            logging.error(f"URL validation failed: {error_message}")
            return render(request, 'import_url.html', {'error': error_message})

        # Check if the URL already exists in VehicleListing
        if import_listing_from_url.objects.filter(url=url).exists():
            return render(request, 'import_url.html', {'error': 'URL already exists in listings'})

        source = import_url.get_import_source_from_url()
        error_message = None
        success=False
        if source == ImportFromSourceOption.GUMTREE:
            try:
                # Call the gumtree_scraper to extract data
                vehicle_listing = get_listings(url)
                print(f"vehicle_listing: {vehicle_listing}")
                if vehicle_listing:
                    print(f"Successfully imported listing from {url}")
                    response=add_product(vehicle_listing)
                    print(f"response: {response}")
                    success=response[0]
                    message=response[1]
                    if success:
                        import_instance = import_listing_from_url.objects.create(url=url, status='Completed', error_message=message)
                    else:
                        import_instance = import_listing_from_url.objects.create(url=url, status='Failed', error_message=message)
                else:
                    logging.warning(f"No details extracted from {url}")
                    import_instance = import_listing_from_url.objects.create(url=url, status='Failed', error_message='No details extracted')
            except Exception as e:
                logging.error(f"Error processing URL {url}: {e}")
                import_instance = import_listing_from_url.objects.create(url=url, status='Error', error_message=str(e))
        elif source == ImportFromSourceOption.FACEBOOK:
            # Call the add_product function and pass the vehicle listing
            result=add_product(vehicle_listing)
            return render(request, 'listings/import_url.html', {'message': 'Vehicle listing added successfully'})
        else:
            logging.info(f"URL is not a supported URL: {url}")
            return render(request, 'listings/import_url.html', {'error': 'Only Gumtree and Facebook URLs are supported'})

    return render(request, 'listings/import_url.html')




def human_like_typing(element, text):
    """Simulate human-like typing with random delays"""
    for char in text:
        element.send_keys(char)
        # Random delay between keystrokes (0.1 to 0.3 seconds)
        time.sleep(random.uniform(0.1, 0.3))

def random_sleep(min_seconds, max_seconds):
    """Sleep for a random amount of time"""
    time.sleep(random.uniform(min_seconds, max_seconds))

def get_2fa_code():
    return input("Please enter your 2FA code: ")

def is_logged_in(driver):
    """Check if already logged into Facebook"""
    try:
        # Try to find elements that only appear when logged in
        WebDriverWait(driver, 5).until(
            EC.presence_of_element_located((
                By.XPATH, 
                "//div[@aria-label='Facebook' or @aria-label='Home' or contains(@class, 'x1qhmfi1')]"
            ))
        )
        return True
    except:
        return False

def create_marketplace_listing(driver, vehicle_listing):
    try:
        driver.get("https://www.facebook.com/marketplace/create/vehicle")
        time.sleep(5)
        
        vehicle_details = {
            "Year": vehicle_listing.year,
            "Make": vehicle_listing.make,
            "Model": vehicle_listing.model,
            "Price": vehicle_listing.price,
            "Location": vehicle_listing.location,
            "Mileage": vehicle_listing.mileage,
            "Description": vehicle_listing.description,
            "Images": vehicle_listing.images,
            "Body style": vehicle_listing.variant,
            "Fuel type": vehicle_listing.fuel_type,
        }

        # Helper function for input fields
        def fill_input_field(field_name, value, selectors, use_suggestion=False, use_tab=False):
            print(f"Entering {field_name}...")
            input_element = None
            
            for selector in selectors:
                try:
                    input_element = WebDriverWait(driver, 5).until(
                        EC.presence_of_element_located((By.XPATH, selector))
                    )
                    break
                except:
                    continue
                    
            if not input_element:
                raise Exception(f"Could not find {field_name} input field")

            driver.execute_script("arguments[0].scrollIntoView(true);", input_element)
            input_element.clear()
            time.sleep(1)
            human_like_typing(input_element, value)
            
            if use_suggestion:
                try:
                    suggestion = WebDriverWait(driver, 5).until(
                        EC.element_to_be_clickable((
                            By.XPATH,
                            f"//div[@role='option' or @role='listbox']//span[contains(text(), '{value}')]"
                        ))
                    )
                    suggestion.click()
                except:
                    input_element.send_keys(Keys.ENTER)
            
            if use_tab:
                input_element.send_keys(Keys.TAB)
            
            time.sleep(2)
            return True

        # Helper function for dropdowns
        def select_dropdown_option(field_name, option_text):
            print(f"Selecting {field_name}...")
            try:
                dropdown = WebDriverWait(driver, 10).until(
                    EC.element_to_be_clickable((
                        By.XPATH,
                        f"//label[@aria-label='{field_name}' and @role='combobox']"
                    ))
                )
                driver.execute_script("arguments[0].scrollIntoView(true);", dropdown)
                time.sleep(1)
                dropdown.click()

                try:
                    option = WebDriverWait(driver, 10).until(
                        EC.element_to_be_clickable((
                            By.XPATH,
                            f"//div[@role='option' or @role='listbox']//span[contains(text(), '{option_text}')]"
                        ))
                    )
                    driver.execute_script("arguments[0].scrollIntoView(true);", option)
                    time.sleep(1)
                    option.click()
                except:
                    actions = ActionChains(driver)
                    actions.send_keys(option_text)
                    actions.send_keys(Keys.ENTER)
                    actions.perform()
                
                time.sleep(2)
                return True
            except Exception as e:
                print(f"Error selecting {field_name}: {str(e)}")
                raise

        # Initial setup
        select_vehicle_type(driver)
        time.sleep(2)

        # Upload images
        image_input = WebDriverWait(driver, 10).until(
            EC.presence_of_element_located((By.XPATH, "//input[@type='file']"))
        )
        image_input.send_keys(os.path.abspath(vehicle_listing.images))
        time.sleep(3)

        # Fill form fields
        select_dropdown_option("Year", int(vehicle_details["Year"]))
        
        # Input fields with their selectors
        input_fields = {
            "Make": [
                "//input[@id=':r31:']",
                "//span[contains(text(), 'Make')]/following-sibling::input",
                "//div[contains(@class, 'xjbqb8w')]//input[contains(@class, 'x1i10hfl')]"
            ],
            "Model": [
                "//input[@id=':r34:']",
                "//span[contains(text(), 'Model')]/following-sibling::input",
                "//div[contains(@class, 'xjbqb8w')]//input[contains(@class, 'x1i10hfl')]"
            ],
            "Mileage": [
                "//input[@id=':r3f:']",
                "//span[contains(text(), 'Mileage')]/following-sibling::input",
                "//div[contains(@class, 'xjbqb8w')]//input[contains(@class, 'x1i10hfl')]"
            ],
            "Price": [
                "//input[@id=':r37:']",
                "//label[@aria-label='Price']//input",
                "//span[contains(text(), 'Price')]/following-sibling::input"
            ],
            "Location": [
                "//input[@id=':r3o:']",
                "//label[@aria-label='Location']//input",
                "//input[@role='combobox' and @aria-label='Location']"
            ],
            "Description": [
                "//textarea[@id=':r49:']",
                "//textarea[contains(@class, 'x1i10hfl')]",
                "//span[contains(text(), 'Description')]/following-sibling::div//textarea"
            ]
        }

        for field, selectors in input_fields.items():
            fill_input_field(
                field, 
                vehicle_details[field],
                selectors,
                use_suggestion=(field in ["Make", "Model", "Location"]),
                use_tab=(field in ["Mileage", "Price", "Description"])
            )

        # Select dropdowns
        select_dropdown_option("Body style", vehicle_details["Body style"])
        select_dropdown_option("Fuel type", vehicle_details["Fuel type"])
        select_dropdown_option("Vehicle condition", "Excelleent")

        # Submit form
        for button_text in ["Next", "Publish"]:
            try:
                button = WebDriverWait(driver, 10).until(
                    EC.element_to_be_clickable((
                        By.XPATH,
                        f"//div[@aria-label='{button_text}' and @role='button']" +
                        f"|//span[contains(text(), '{button_text}')]/ancestor::div[@role='button']"
                    ))
                )
                driver.execute_script("arguments[0].scrollIntoView(true);", button)
                time.sleep(1)
                
                for _ in range(3):
                    try:
                        button.click()
                        break
                    except:
                        time.sleep(1)
                        continue
                
                time.sleep(5 if button_text == "Publish" else 3)
            except:
                return False, f"Failed to click {button_text} button"

        return True, "Listing created successfully"

    except Exception as e:
        print(f"Error in create_marketplace_listing: {str(e)}")
        return False, str(e)

def select_vehicle_type(driver):
    try:
        print("Waiting for vehicle type options to load...")
        # Wait for and click the vehicle type dropdown
        vehicle_dropdown = WebDriverWait(driver, 10).until(
            EC.element_to_be_clickable((
                By.XPATH, 
                "//span[contains(text(), 'Vehicle type')]/ancestor::label"
            ))
        )
        time.sleep(2)
        vehicle_dropdown.click()
        print("Clicked vehicle type dropdown")

        # Wait for and click the "Car/Truck" option
        car_option = WebDriverWait(driver, 10).until(
            EC.element_to_be_clickable((
                By.XPATH, 
                f"//div[@role='option'][contains(.,'Car/Truck')]"
            ))
        )
        time.sleep(1)
        car_option.click()
        print(f"Selected Car/Truck option")

        return True, f"Vehicle type (Car/Truck) selected successfully"
    except Exception as e:
        print(f"Error selecting vehicle type: {str(e)}")
        return False, str(e)


def facebook_login(vehicle_listing):
    global global_driver
    
    try:
        # Check if browser is already open
        if global_driver:
            try:
                # Check if session is still valid
                global_driver.current_url
                if is_logged_in(global_driver):
                    print("Already logged in, navigating to Marketplace vehicle creation...")
                    global_driver.get("https://www.facebook.com/marketplace/create/vehicle")
                    time.sleep(5)
                    print("Navigated to Marketplace vehicle creation")
                    # Create marketplace listing
                    result=create_marketplace_listing(global_driver, vehicle_listing)
                    return result
            except:
                # Session is invalid, close the old driver
                try:
                    global_driver.quit()
                except:
                    pass
                global_driver = None

        # Set up Chrome options
        chrome_options = Options()
        chrome_options.add_argument('--start-maximized')
        chrome_options.add_argument('--disable-notifications')
        
        # Remove automation flags
        chrome_options.add_argument('--disable-blink-features=AutomationControlled')
        chrome_options.add_experimental_option("excludeSwitches", ["enable-automation"])
        chrome_options.add_experimental_option('useAutomationExtension', False)
        chrome_options.add_experimental_option("detach", True)
        
        # Set default download directory and other preferences
        prefs = {
            "profile.default_content_settings.popups": 0,
            "download.default_directory": os.getcwd(),
            "directory_upgrade": True
        }
        chrome_options.add_experimental_option("prefs", prefs)
        
        # Use existing Chrome profile with absolute path
        user_data_dir = os.path.abspath(os.path.join(os.getcwd(), 'chrome-profile'))
        if not os.path.exists(user_data_dir):
            os.makedirs(user_data_dir)
        chrome_options.add_argument(f'--user-data-dir={user_data_dir}')
        chrome_options.add_argument('--profile-directory=Default')
        
        # Initialize Chrome driver
        print("Initializing Chrome driver...")
        service = Service(ChromeDriverManager().install())
        driver = webdriver.Chrome(service=service, options=chrome_options)
        
        # Set page load timeout
        driver.set_page_load_timeout(30)
        
        # Store the driver globally
        global_driver = driver
        
        # Maximize window
        driver.maximize_window()
        
        print("Navigating to Facebook...")
        # Navigate to Facebook with error handling
        try:
            driver.get("https://www.facebook.com")
        except Exception as e:
            print(f"Error loading Facebook: {str(e)}")
            driver.refresh()  # Try refreshing if initial load fails
            
        time.sleep(5)  # Wait for page to load properly
        
        # Ensure we're on Facebook
        current_url = driver.current_url
        if "facebook.com" not in current_url:
            print(f"Not on Facebook. Current URL: {current_url}")
            driver.get("https://www.facebook.com")
            time.sleep(5)
        
        # Check if already logged in
        if is_logged_in(driver):
            print("Login successful, navigating to Marketplace vehicle creation...")
            time.sleep(3)
            print("Navigated to Marketplace vehicle creation")
            result=create_marketplace_listing(driver, vehicle_listing)
            return result
        
        # If not logged in, proceed with login
        print("Not logged in, proceeding with login...")
        email = "usamakaleem322@gmail.com"
        password = "usama;;743,,"
        
        # Handle cookie consent if present
        try:
            cookie_buttons = driver.find_elements(By.XPATH, 
                "//button[contains(string(), 'Allow') or contains(string(), 'Accept') or contains(string(), 'Okay')]"
            )
            if cookie_buttons:
                time.sleep(random.uniform(0.5, 1.5))
                cookie_buttons[0].click()
        except:
            print("No cookie banner found or already accepted")
        
        try:
            # Wait for and fill email field
            print("Looking for email field...")
            email_field = WebDriverWait(driver, 10).until(
                EC.presence_of_element_located((By.ID, "email"))
            )
            email_field.clear()
            human_like_typing(email_field, email)
            
            # Fill password field
            print("Entering password...")
            password_field = driver.find_element(By.ID, "pass")
            password_field.clear()
            human_like_typing(password_field, password)
            
            # Click login button
            print("Clicking login button...")
            login_button = driver.find_element(By.NAME, "login")
            login_button.click()
            
            time.sleep(5)  # Wait for login process
            
            # Handle 2FA if needed
            try:
                two_fa_input = WebDriverWait(driver, 10).until(
                    EC.presence_of_element_located((
                        By.XPATH, 
                        "//input[contains(@aria-label, 'code') or contains(@placeholder, 'code')]"
                    ))
                )
                print("2FA detected - Please enter the code manually...")
                WebDriverWait(driver, 60).until(lambda x: is_logged_in(x))
                
            except:
                print("No 2FA required or already handled")
            
            # Verify login success
            if is_logged_in(driver):
                print("Login successful, navigating to Marketplace vehicle creation...")
                time.sleep(3)

               # Create marketplace listing
                result=create_marketplace_listing(driver, vehicle_listing)
                return result
            else:
                raise Exception("Login failed")
            
        except Exception as e:
            print(f"Login process error: {str(e)}")
            raise
            
    except Exception as e:
        print(f"Error occurred: {str(e)}")
        if 'driver' in locals():
            driver.quit()
        if global_driver:
            global_driver = None
        return 'error : {str(e)}'


def logout(request):
    """Handle logout and close browser"""
    global global_driver
    try:
        if global_driver:
            # Try to logout from Facebook first
            try:
                global_driver.get("https://www.facebook.com")
                menu_button = WebDriverWait(global_driver, 10).until(
                    EC.element_to_be_clickable((By.XPATH, "//div[@aria-label='Account' or @aria-label='Menu']"))
                )
                menu_button.click()
                time.sleep(2)
                
                logout_button = WebDriverWait(global_driver, 10).until(
                    EC.element_to_be_clickable((By.XPATH, "//span[contains(text(), 'Log Out')]"))
                )
                logout_button.click()
                time.sleep(2)
            except:
                print("Could not perform Facebook logout")
            
            # Close browser
            global_driver.quit()
            global_driver = None
            
        return render(request, 'auto_login/index.html', {
            'status': 'success',
            'message': 'Logged out and browser closed'
        })
    except Exception as e:
        return render(request, 'auto_login/index.html', {
            'status': 'error',
            'message': f'Error during logout: {str(e)}'
        })

# def index(request):
#     """Render the index page"""
#     return render(request, 'auto_login/index.html')

def add_product(vehicle_listing):
    """Handle add product button click"""
    global global_driver
    print(f"vehicle_listing: {vehicle_listing}")
    if not global_driver or not is_logged_in(global_driver):
        print("No active session, initiating login...")
        # Call facebook_login to handle the login process
        response = facebook_login(vehicle_listing)
        return response
    return create_marketplace_listing(global_driver, vehicle_listing)  # This will trigger your existing facebook_login view

def delete_product(request):
    """Handle delete product functionality"""
    global global_driver
    
    try:
        # First check if we have an active session
        if not global_driver or not is_logged_in(global_driver):
            print("No active session, initiating login...")
            # Call facebook_login to handle the login process
            response = facebook_login(request)
            
            # Check if login was successful
            if not is_logged_in(global_driver):
                return render(request, 'auto_login/index.html', {
                    'status': 'error',
                    'message': 'Failed to login to Facebook'
                })
        
        driver = global_driver
        print("Logged in successfully, proceeding to delete listings...")

        # Navigate to selling page
        print("Navigating to selling page...")
        driver.get("https://www.facebook.com/marketplace/you/selling")
        time.sleep(5)  # Wait for page to load

        # Find all product listings
        print("Finding product listings...")
        listings = WebDriverWait(driver, 10).until(
            EC.presence_of_all_elements_located((
                By.XPATH, 
                "//div[contains(@class, 'x1n2onr6')]//div[contains(@class, 'x1ja2u2z')]//div[@aria-label and contains(@aria-label, 'More options')]"
            ))
        )

        deleted_count = 0
        for listing in listings:
            try:
                # Click the menu button (three dots)
                print(f"Clicking menu for listing {deleted_count + 1}")
                driver.execute_script("arguments[0].scrollIntoView(true);", listing)
                time.sleep(1)
                listing.click()
                time.sleep(2)

                # Click Delete listing option
                print("Clicking delete option")
                delete_option = WebDriverWait(driver, 10).until(
                    EC.element_to_be_clickable((
                        By.XPATH,
                        "//div[@role='menuitem']//span[contains(text(), 'Delete listing')]//ancestor::div[@role='menuitem']"
                    ))
                )
                delete_option.click()
                time.sleep(2)

                # Click final Delete button in confirmation modal
                print("Confirming deletion")
                time.sleep(2)  # Wait for modal to be fully loaded
                confirm_delete = WebDriverWait(driver, 10).until(
                    EC.element_to_be_clickable((
                        By.XPATH,
                        "//div[@aria-label='Delete' and contains(@class, 'x1i10hfl') and contains(@class, 'xjbqb8w') and @role='button' and @tabindex='0']"
                    ))
                )
                driver.execute_script("arguments[0].click();", confirm_delete)
                time.sleep(3)

                deleted_count += 1
                print(f"Successfully deleted listing {deleted_count}")

            except Exception as e:
                print(f"Error deleting listing: {str(e)}")
                continue

        return render(request, 'auto_login/index.html', {
            'status': 'success',
            'message': f'Successfully deleted {deleted_count} listings',
            'browser_open': True
        })

    except Exception as e:
        print(f"Error in delete_product: {str(e)}")
        return render(request, 'auto_login/index.html', {
            'status': 'error',
            'message': f'Error during deletion: {str(e)}'
        })