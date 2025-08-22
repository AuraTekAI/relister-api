import logging
import random
import time
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError
import os
import requests
from relister.settings import IMAGES_DIR
import re
from django.conf import settings
from VehicleListing.models import FacebookUserCredentials
from django.utils import timezone
from .utils import handle_retry_or_disable_credentials,should_delete_listing

logging = logging.getLogger('facebook')
def human_like_typing(element, text):
    """Simulate human-like typing with random delays."""
    for char in text:
        element.type(char, delay=random.uniform(settings.LONG_DELAY_START_TIME_BETWEEN_ELEMENTS_SELECTION, settings.LONG_DELAY_END_TIME_BETWEEN_ELEMENTS_SELECTION))  
        time.sleep(random.uniform(0.0005, 0.005))

def random_sleep(min_seconds, max_seconds):
    """Sleep for a random amount of time."""
    time.sleep(random.uniform(min_seconds, max_seconds))

def fill_input_field(page, field_name, value, selectors, use_suggestion=False, use_tab=False):
    """Helper function to fill input fields with human-like typing."""
    logging.info(f"Entering {field_name}...")
    input_element = None

    for selector in selectors:
        try:
            input_element = page.locator(selector).first
            input_element.scroll_into_view_if_needed()
            input_element.clear()
            human_like_typing(input_element, value)  
            logging.info(f"{field_name} filled successfully.")
            break
        except Exception as e:
            logging.warning(f"Failed with selector {selector}: {e}")
            continue

    if not input_element:
        raise Exception(f"Could not find {field_name} input field")

    if use_suggestion:
        try:
            city = value.split(',')[0].strip()
            suggestion = page.locator(f"//div[@role='option' or @role='listbox']//span[contains(text(), '{city}')]").first
            suggestion.click()
        except:
            input_element.press("Enter")

    if use_tab:
        input_element.press("Tab")

    random_sleep(settings.SHORT_DELAY_START_TIME_BETWEEN_ELEMENTS_SELECTION, settings.SHORT_DELAY_END_TIME_BETWEEN_ELEMENTS_SELECTION)  # Random delay after filling the field
    return True

def select_dropdown_option(page, field_name, option_text):
    """Selects a dropdown option with retries, visibility checks, and logging."""
    max_retries = settings.MAX_RETRIES_ATTEMPTS
    logging.info(f"Selecting '{field_name}' with option '{option_text}'...")

    dropdown_selectors = [
        f"//span[contains(text(), '{field_name}')]/ancestor::label",
        f"//label[@aria-label='{field_name}' and @role='combobox']",
        f"//span[text()='{field_name}']/ancestor::label[@role='combobox']",
        f"//div[contains(@class, 'x1n2onr6')]//label[contains(.,'{field_name}')]",
        f"//label[contains(., '{field_name}')][@role='combobox']",
        f"//span[contains(text(), '{field_name}')]/ancestor::label"
    ]

    option_selectors = [
        f"//div[@role='option' or @role='listbox']//span[contains(text(), '{option_text}')]",
        f"//div[@role='option'][contains(.,'{option_text}')]",
        f"//div[@role='option' or @role='listbox'][contains(.,'{option_text}')]"
    ]

    for attempt in range(1, max_retries + 1):
        logging.info(f"Attempt {attempt} of {max_retries} to select '{field_name}'.")

        try:
            dropdown = None
            for selector in dropdown_selectors:
                try:
                    dropdown = page.locator(selector).first
                    dropdown.wait_for(state="visible", timeout=5000)
                    dropdown.scroll_into_view_if_needed()
                    dropdown.click()
                    logging.debug(f"Clicked dropdown using selector: {selector}")
                    random_sleep(settings.SHORT_DELAY_START_TIME_BETWEEN_ELEMENTS_SELECTION, settings.SHORT_DELAY_END_TIME_BETWEEN_ELEMENTS_SELECTION)
                    break
                except Exception as e:
                    logging.debug(f"Dropdown selector failed: {selector} => {e}")

            if not dropdown:
                raise Exception(f"Unable to locate visible dropdown for field '{field_name}'")

            # Try selecting the option
            for option_selector in option_selectors:
                try:
                    option = page.locator(option_selector).first
                    option.wait_for(state="visible", timeout=5000)
                    option.scroll_into_view_if_needed()
                    option.click()
                    logging.info(f"Option '{option_text}' selected for field '{field_name}'.")
                    random_sleep(settings.SHORT_DELAY_START_TIME_BETWEEN_ELEMENTS_SELECTION, settings.SHORT_DELAY_END_TIME_BETWEEN_ELEMENTS_SELECTION)
                    return True, f"{field_name} = {option_text} selected successfully"
                except Exception as e:
                    logging.debug(f"Option selector failed: {option_selector} => {e}")
                    continue

            # Fallback: Fill and Enter
            dropdown.fill(option_text)
            dropdown.press("Enter")
            logging.info(f"Filled and submitted '{option_text}' for field '{field_name}'.")
            random_sleep(settings.SHORT_DELAY_START_TIME_BETWEEN_ELEMENTS_SELECTION, settings.SHORT_DELAY_END_TIME_BETWEEN_ELEMENTS_SELECTION)
            return True, f"{field_name} = {option_text} entered manually"

        except PlaywrightTimeoutError as te:
            logging.warning(f"Timeout on attempt {attempt} for field '{field_name}': {te}")
        except Exception as e:
            logging.error(f"Error on attempt {attempt} for field '{field_name}': {e}")

        if attempt < max_retries:
            wait_time = random.uniform(settings.SHORT_DELAY_START_TIME_BETWEEN_ELEMENTS_SELECTION, settings.SHORT_DELAY_END_TIME_BETWEEN_ELEMENTS_SELECTION)
            logging.info(f"Retrying after {wait_time:.2f}s...")
            time.sleep(wait_time)
        else:
            error_msg = f"Failed to select '{field_name}' with option '{option_text}' after {max_retries} attempts."
            logging.critical(error_msg)
            return False, error_msg


def select_vehicle_type(page):
    """Select the vehicle type (Car/Truck) with retries, timeout handling, and enhanced logging."""
    logging.info("Attempting to select vehicle type...")
    max_retries = 3
    for attempt in range(1, max_retries + 1):
        try:
            logging.info(f"Attempt {attempt} of {max_retries}")

            vehicle_dropdown = page.locator("//span[contains(text(), 'Vehicle type')]/ancestor::label").first
            vehicle_dropdown.wait_for(state="visible", timeout=7000)
            vehicle_dropdown.scroll_into_view_if_needed()
            vehicle_dropdown.click()
            logging.debug("Clicked on vehicle type dropdown.")
            random_sleep(settings.SHORT_DELAY_START_TIME_BETWEEN_ELEMENTS_SELECTION, settings.SHORT_DELAY_END_TIME_BETWEEN_ELEMENTS_SELECTION)

            car_option = page.locator("//div[@role='option'][contains(.,'Other')]").first
            car_option.wait_for(state="visible", timeout=7000)
            car_option.scroll_into_view_if_needed()
            car_option.click()
            logging.info("Vehicle type (Other) selected successfully.")
            random_sleep(settings.SHORT_DELAY_START_TIME_BETWEEN_ELEMENTS_SELECTION, settings.SHORT_DELAY_END_TIME_BETWEEN_ELEMENTS_SELECTION)

            return True, "Other"

        except PlaywrightTimeoutError as te:
            logging.warning(f"Timeout waiting for element on attempt {attempt}: {te}")
        except Exception as e:
            logging.error(f"Error on attempt {attempt}: {e}")

        if attempt < max_retries:
            wait_time = random.uniform(settings.SHORT_DELAY_START_TIME_BETWEEN_ELEMENTS_SELECTION, settings.SHORT_DELAY_END_TIME_BETWEEN_ELEMENTS_SELECTION)
            logging.info(f"Retrying after {wait_time:.2f} seconds...")
            time.sleep(wait_time)
        else:
            logging.info("Failed to select vehicle type after all retries.")
            return False, "Failed to select vehicle type after all retries."

def handle_login_info_modal(page):
    """Handle the 'Save your login info' modal if it appears."""
    try:
        # Check if modal exists with a short timeout
        modal_exists = page.wait_for_selector("//span[contains(text(), 'Save your login info')]", timeout=3000, state="visible")
        
        if modal_exists:
            logging.info("Login info modal detected, attempting to close")
            try:
                # Try to click "Not now" button
                not_now_button = page.locator("//span[text()='Not now']/ancestor::div[@role='button']").first
                if not_now_button:
                    not_now_button.click(force=True)
                    logging.info("Clicked 'Not now' button")
                    random_sleep(settings.SHORT_DELAY_START_TIME_BETWEEN_ELEMENTS_SELECTION, settings.SHORT_DELAY_END_TIME_BETWEEN_ELEMENTS_SELECTION)
            except Exception as e:
                logging.warning(f"Failed to click 'Not now': {e}")
                # Try close button as fallback
                try:
                    close_button = page.locator("div[aria-label='Close'][role='button']").first
                    close_button.click(force=True)
                    logging.info("Clicked close button")
                    random_sleep(settings.SHORT_DELAY_START_TIME_BETWEEN_ELEMENTS_SELECTION, settings.SHORT_DELAY_END_TIME_BETWEEN_ELEMENTS_SELECTION)
                except Exception as e:
                    logging.warning(f"Failed to click close button: {e}")
    except Exception:
        logging.info("No login modal detected, proceeding with form")
    
    return True

def is_element_visible(page, selector):
    """Check if an element is present and visible on the page."""
    try:
        element = page.locator(selector).first
        return element.is_visible()
    except Exception as e:
        logging.warning(f"Error checking element visibility: {e}")
        return False
    

def click_button_when_enabled(page, button_text: str, max_attempts=3, wait_time=3):
    try:
        # Locate the button using XPath
        button = page.locator(
            f"//div[@aria-label='{button_text}' and @role='button']"
            f"|//span[contains(text(), '{button_text}')]/ancestor::div[@role='button']"
        ).first

        button.scroll_into_view_if_needed()

        logging.info(f"Trying to click '{button_text}' button...")

        for attempt in range(1, max_attempts + 1):
            is_disabled = button.get_attribute("aria-disabled")

            logging.info(f"[Attempt {attempt}] '{button_text}' button status: {'disabled' if is_disabled == 'true' else 'enabled'}")

            if is_disabled != "true":
                button.click()
                logging.info(f" Successfully clicked '{button_text}' button.")
                return True, f"Clicked '{button_text}'"
            
            logging.debug(f"Waiting {wait_time}s before retrying...")
            time.sleep(wait_time)

        logging.error(f"'{button_text}' button never became enabled after {max_attempts} attempts.")
        return False, f"'{button_text}' button is disabled"

    except Exception as e:
        logging.exception(f"Failed to click '{button_text}' button due to exception: {e}")
        return False, f"Exception occurred while clicking '{button_text}'"
    


def handle_make_field(page, make_value):
    """Handles the 'Make' field by checking for input field first, then dropdown if input not found."""
    logging.info(f"Handling 'Make' field with value: {make_value}")

    vehicle_make_list = [
        "Alfa Romeo", "Alpina", "Aston Martin", "Bentley", "Chrysler", "Daewoo", "Ferrari", "FIAT", "Dodge", "Ford",
        "Honda", "Hyundai", "Hummer", "INFINITI", "Isuzu", "Jaguar", "Jeep", "Kia", "Lamborghini", "Land Rover",
        "Lexus", "Lotus", "MINI", "Mercedes-Benz", "Maserati", "McLaren", "Mitsubishi", "Nissan", "Plymouth",
        "Pontiac", "Porsche", "Rolls-Royce", "Saab", "Smart", "Subaru", "Suzuki", "Toyota", "Tesla", "Volkswagen", "Volvo"
    ]
    lower_make_list = [m.lower() for m in vehicle_make_list]
    make_lower = make_value.lower()

    # Step 1: Try input field first
    make_input_selectors = [
        "//span[contains(text(), 'Make')]/following-sibling::input"
    ]

    logging.info("Trying input selectors for 'Make'...")
    for selector in make_input_selectors:
        try:
            input_element = page.locator(selector).first
            if input_element.is_visible():
                input_element.scroll_into_view_if_needed()
                input_element.clear()
                human_like_typing(input_element, make_value)
                logging.info("Filled 'Make' via input: %s", make_value)
                random_sleep(settings.LONG_DELAY_START_TIME_BETWEEN_ELEMENTS_SELECTION, settings.LONG_DELAY_END_TIME_BETWEEN_ELEMENTS_SELECTION)
                return True
        except Exception as e:
            logging.warning(f"Failed input selector {selector}: {e}")

    # Step 2: Try dropdown as fallback
    logging.info("Input not found. Trying dropdown for 'Make'...")
    dropdown_selectors = [
        "//span[contains(text(), 'Make')]/ancestor::label",
        "//label[@aria-label='Make' and @role='combobox']",
        "//span[text()='Make']/ancestor::label[@role='combobox']",
        "//div[contains(@class, 'x1n2onr6')]//label[contains(.,'Make')]",
        "//label[contains(., 'Make')][@role='combobox']",
        "//span[contains(text(), 'Make')]/ancestor::label"
    ]

    if make_lower in lower_make_list:
        display_make = vehicle_make_list[lower_make_list.index(make_lower)]
        for selector in dropdown_selectors:
            try:
                dropdown = page.locator(selector).first
                if dropdown.is_visible():
                    dropdown.scroll_into_view_if_needed()
                    dropdown.click()
                    random_sleep(settings.LONG_DELAY_START_TIME_BETWEEN_ELEMENTS_SELECTION, settings.LONG_DELAY_END_TIME_BETWEEN_ELEMENTS_SELECTION)

                    # Try to select option
                    option_selectors = [
                        f"//div[@role='option' or @role='listbox']//span[contains(text(), '{display_make}')]",
                        f"//div[@role='option'][contains(.,'{display_make}')]",
                        f"//div[@role='option' or @role='listbox'][contains(.,'{display_make}')]"
                    ]
                    for option_selector in option_selectors:
                        try:
                            option = page.locator(option_selector).first
                            if option.is_visible():
                                option.scroll_into_view_if_needed()
                                option.click()
                                logging.info(f"Selected 'Make' from dropdown: {display_make}")
                                random_sleep(settings.LONG_DELAY_START_TIME_BETWEEN_ELEMENTS_SELECTION, settings.LONG_DELAY_END_TIME_BETWEEN_ELEMENTS_SELECTION)
                                return True
                        except Exception as e:
                            logging.warning(f"Failed option selector {option_selector}: {e}")
                            continue

                    # As fallback: type and press Enter
                    dropdown.fill(display_make)
                    dropdown.press("Enter")
                    logging.info("↩️ Typed and entered value in dropdown fallback")
                    random_sleep(settings.SHORT_DELAY_START_TIME_BETWEEN_ELEMENTS_SELECTION, settings.SHORT_DELAY_END_TIME_BETWEEN_ELEMENTS_SELECTION)
                    return True
            except Exception as e:
                logging.warning(f"Failed dropdown selector {selector}: {e}")
                continue
    else:
        logging.warning(f"Make '{make_value}' not recognized.")
        return False, f"Make '{make_value}' not in supported list"

    logging.error("Failed to handle 'Make' field.")
    return False, "Failed to locate or interact with 'Make' field"


def verify_uploaded_images_and_check_limit(page, max_images_threshold):
    try:
        logging.info("Checking for uploaded image elements...")

        # Optimized selector targeting Facebook CDN image uploads
        selector = "div.x1n2onr6.xh8yej3 img[src^='https://scontent']"

        # Wait for any uploaded image to appear
        page.wait_for_selector(selector, timeout=10000)
        images = page.query_selector_all(selector)
        logging.info(f"Found total image elements: {len(images)}")

        unique_valid_urls = set()

        for idx, img in enumerate(images):
            src = img.get_attribute("src")
            is_visible = img.is_visible()
            is_loaded = page.evaluate("(img) => img.complete && img.naturalWidth > 0", img)

            if is_visible and is_loaded and src:
                unique_valid_urls.add(src)

        logging.info(f"Unique, visible & loaded image URLs: {len(unique_valid_urls)}")

        if len(unique_valid_urls) == max_images_threshold:
            logging.info(f" {len(unique_valid_urls)} images found (threshold: {max_images_threshold})")
            return True
        else:
            logging.info(f"FAILED: {len(unique_valid_urls)} images found (threshold: {max_images_threshold})")
            return False

    except PlaywrightTimeoutError:
        logging.info("Timeout: No uploaded image found")
        return False

def create_marketplace_listing(vehicle_listing,session_cookie):
    """Create a new listing on Facebook Marketplace with human-like interactions."""
    try:    
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True,args=["--start-maximized"])
            context = browser.new_context(storage_state=session_cookie,viewport={"width": 1920, "height": 1080})
            page = context.new_page()

            # Navigate to the vehicle listing page
            try:
                page.goto("https://www.facebook.com/marketplace/create/vehicle", timeout=60000)
            except Exception as e:
                logging.error(f"Timeout error navigating to Facebook Marketplace vehicle listing page: {e}")
                browser.close()
                return False, "Timeout error navigating to Facebook Marketplace vehicle listing page"
            logging.info("Navigated to Facebook Marketplace vehicle listing page.")
            random_sleep(settings.LONG_DELAY_START_TIME_BETWEEN_ELEMENTS_SELECTION, settings.LONG_DELAY_END_TIME_BETWEEN_ELEMENTS_SELECTION)  # Random delay after page load
            logging.info("Page loaded successfully.")
            logging.info(f"create market place listing for {vehicle_listing.list_id} and for user {vehicle_listing.user.email} and vehicle title is {vehicle_listing.year} {vehicle_listing.make} {vehicle_listing.model}")
            # Quick check for modal and handle if exists
            handle_login_info_modal(page)

            # Check if the "Limit reached" element is visible
            limit_reached_selector = "//span[@class='x1lliihq x6ikm8r x10wlt62 x1n2onr6']//span[contains(@class, 'x193iq5w') and text()='Limit reached']"
            if is_element_visible(page, limit_reached_selector):
                logging.info("Limit reached element is visible.")
                browser.close()
                return False, "Limit reached"
            
            # Vehicle details
            vehicle_details = {
                "Year": vehicle_listing.year,
                "Make": vehicle_listing.make,
                "Model": vehicle_listing.model,
                "Price": str(vehicle_listing.price) if vehicle_listing.price else None,
                "Location": vehicle_listing.location,
                "Mileage": str(vehicle_listing.mileage) if vehicle_listing.mileage else None,
                "Description": vehicle_listing.description if vehicle_listing.description else "No description provided."
            }
            if vehicle_details["Mileage"]:
                description_lines = vehicle_details["Description"].splitlines()
                mileage_text = "Mileage: " + vehicle_details["Mileage"] + "km"

                # Check if mileage is already in description (case-insensitive)
                if mileage_text.lower() not in vehicle_details["Description"].lower():
                    # Insert mileage as the first line
                    description_lines.insert(0, mileage_text)                        
    
                    # Update the description
                    vehicle_details["Description"] = "\n".join(description_lines)

            # Select vehicle type
            result = select_vehicle_type(page)
            if result[0]:
                logging.info(f"Vehicle type selected successfully: {result[1]}")
            else:
                logging.info(f"Failed to select vehicle type: {result[1]}")
                browser.close()
                return False, result[1]
            random_sleep(settings.LONG_DELAY_START_TIME_BETWEEN_ELEMENTS_SELECTION, settings.LONG_DELAY_END_TIME_BETWEEN_ELEMENTS_SELECTION)

            index = 0
            if vehicle_listing.images:
                # Download the images using url and save it locally    
                for image_url in vehicle_listing.images:
                    if index > 18:
                        break
                    image_name = os.path.basename(image_url)
                    image_extension = os.path.splitext(image_name)[1] 
                    index = index + 1
                    new_image_name = f"{vehicle_listing.list_id}_image{image_extension}index{index}"  
                    local_image_path = os.path.join(IMAGES_DIR, new_image_name)

                    try:
                        # Download the image
                        image_response = requests.get(image_url)
                        image_response.raise_for_status()
                        with open(local_image_path, "wb") as file:
                            file.write(image_response.content)
                    except requests.exceptions.RequestException as e:
                        return False, f"Error downloading the image: {e}"
                    # Check if the image was downloaded successfully
                    if not os.path.exists(local_image_path):
                        browser.close()
                        return False, "Image download failed, file does not exist." 

                    # Upload images
                    image_input = page.locator("//input[@type='file']").first
                    random_sleep(settings.LONG_DELAY_START_TIME_BETWEEN_ELEMENTS_SELECTION, settings.LONG_DELAY_END_TIME_BETWEEN_ELEMENTS_SELECTION)
                    image_input.set_input_files(local_image_path)
                    logging.info("Photos uploaded successfully.")
                    random_sleep(settings.LONG_DELAY_START_TIME_BETWEEN_ELEMENTS_SELECTION, settings.LONG_DELAY_END_TIME_BETWEEN_ELEMENTS_SELECTION)  # Random delay after uploading images
            else:
                logging.info("No images found.")
                browser.close()
                return False, "No images found."
            random_sleep(settings.LONG_DELAY_START_TIME_BETWEEN_ELEMENTS_SELECTION, settings.LONG_DELAY_END_TIME_BETWEEN_ELEMENTS_SELECTION)

            result = select_dropdown_option(page, "Year", vehicle_details["Year"])
            if result[0]:
                logging.info(f"Year selected successfully: {result[1]}")
            else:
                logging.info(f"Failed to select Year: {result[1]}")
                browser.close()
                return False, result[1]
            random_sleep(settings.LONG_DELAY_START_TIME_BETWEEN_ELEMENTS_SELECTION, settings.LONG_DELAY_END_TIME_BETWEEN_ELEMENTS_SELECTION)
            handle_make_field(page, vehicle_listing.make)
            random_sleep(settings.LONG_DELAY_START_TIME_BETWEEN_ELEMENTS_SELECTION, settings.LONG_DELAY_END_TIME_BETWEEN_ELEMENTS_SELECTION)

            # Input fields with their selectors
            input_fields = {
                "Model": [
                    # "//input[@id=':r34:']",
                    "//span[contains(text(), 'Model')]/following-sibling::input",
                    "//div[contains(@class, 'xjbqb8w')]//input[contains(@class, 'x1i10hfl')]"
                ],
                "Price": [
                    # "//input[@id=':r37:']",
                    # "//label[@aria-label='Price']//input",
                    "//span[contains(text(), 'Price')]/following-sibling::input"
                ],
                "Location": [
                    # "//input[@id=':r3o:']",
                    # "//label[@aria-label='Location']//input",
                    "//span[contains(text(), 'Location')]/following-sibling::input",
                    "//input[@role='combobox' and @aria-label='Location']"
                ],
                "Description": [
                    # "//textarea[@id=':r49:']",
                    "//textarea[contains(@class, 'x1i10hfl')]",
                    "//span[contains(text(), 'Description')]/following-sibling::div//textarea"
                ]
            }

            for field, selectors in input_fields.items():
                if field == "Location" and not vehicle_details["Location"]:
                    continue
                fill_input_field(
                    page,
                    field,
                    vehicle_details[field],
                    selectors,
                    use_suggestion=(field in [ "Model", "Location"]),
                    use_tab=(field in ["Price", "Description"])

                )
            
            result = verify_uploaded_images_and_check_limit(page, index)
            if result:
                logging.info("Images verified and uploaded successfully.")
            else:
                logging.info("Images failed to upload.")
                browser.close()
                return False, "Images failed to upload."
                

            # Submit form
            for button_text in ["Next", "Publish"]:
                random_sleep(settings.DELAY_START_TIME_FOR_LOADING_PAGE, settings.DELAY_END_TIME_FOR_LOADING_PAGE)
                success, message = click_button_when_enabled(page, button_text, max_attempts=3, wait_time=3)
                if not success:
                    # Optionally handle the failure
                    browser.close()
                    return False, message
                else:
                    # Add random sleep if needed
                    random_sleep(settings.DELAY_START_TIME_FOR_LOADING_PAGE, settings.DELAY_END_TIME_FOR_LOADING_PAGE)

            # Close browser
            browser.close()
            logging.info("Browser closed successfully.")
            # Delete the images
            index = 0
            for image_url in vehicle_listing.images:
                if index > 18:
                    break
                image_name = os.path.basename(image_url)
                image_extension = os.path.splitext(image_name)[1] 
                index = index + 1
                new_image_name = f"{vehicle_listing.list_id}_image{image_extension}index{index}"  
                local_image_path = os.path.join(IMAGES_DIR, new_image_name)
                if os.path.exists(local_image_path):
                    os.remove(local_image_path)
            logging.info("Image file deleted successfully.")
            return True, "Listing created successfully"

    except Exception as e:
        logging.error(f"Error in create_marketplace_listing: {e}")
        return False, str(e)

def is_logged_in(page):
    """Check if the user is logged in."""
    try:
        page.wait_for_selector("//div[@aria-label='Facebook' or @aria-label='Home' or contains(@class, 'x1qhmfi1')]", timeout=30000)
        return True
    except:
        return False

def handle_cookie_consent(page):
    """Handle cookie consent popup if present."""
    try:
        cookie_buttons = page.locator(
            "//button[contains(text(), 'Allow') or contains(text(), 'Accept') or contains(text(), 'Okay')]"
        )
        if cookie_buttons.count() > 0:
            random_sleep(settings.SHORT_DELAY_START_TIME_BETWEEN_ELEMENTS_SELECTION, settings.SHORT_DELAY_END_TIME_BETWEEN_ELEMENTS_SELECTION)
            cookie_buttons.first.click()
            logging.info("Cookie consent handled.")
    except Exception as e:
        logging.warning(f"No cookie banner found or already accepted: {e}")



def remove_prefix_case_insensitive(title: str, prefix: str) -> str:
    """
    Removes the given prefix from the start of the title, ignoring case.
    Preserves the original casing of the remaining title.
    """
    title_stripped = title.lstrip()
    prefix_len = len(prefix)

    if title_stripped.lower().startswith(prefix.lower()):
        return title_stripped[prefix_len:].lstrip()

    return title_stripped


def extract_listings_with_status(text):
    """
    Extracts structured listings including title, price, date, and status.
    Returns a list of dicts: title, price, listing_date (in DD/MM), status.
    """
    listings = []

    # Define fallback regex patterns from most specific to most generic
    patterns = [
        # Pattern 1: AU$ version (fallback)
        (r'(.*?)(AU\$\d{1,3}(?:,\d{3})*).*?Listed on (\d{2}/\d{2})(.*?)(?=(?:\d{4}|\Z))', False),
        # Pattern 2: With tip
        (r'(?:Tip:.*?\?)?\s*(.*?)A\$([\d,]+).*?Listed on (\d{1,2}/\d{1,2}).*?(Mark as sold|Mark as available)', True),
        # Pattern 3: Without tip
        (r'(.*?)A\$([\d,]+).*?Listed on (\d{1,2}/\d{1,2}).*?(Mark as sold|Mark as available)', True)
    ]

    for pattern, convert_date in patterns:
        matches = re.findall(pattern, text, re.DOTALL)
        if matches:
            for match in matches:
                # Old pattern where status needs to be inferred
                title, price, date, tail = match
                tail_clean = tail.lower().replace('\xa0', ' ')
                if "mark as sold" in tail_clean:
                    status = "Mark as sold"
                elif "mark as available" in tail_clean:
                    status = "Mark as available"
                else:
                    status = None
                # Format date to DD/MM if needed
                if convert_date:
                    month, day = date.strip().split('/')
                    date = f"{day.zfill(2)}/{month.zfill(2)}"
                title = title.strip().replace('\xa0', ' ')
                clean_title = remove_prefix_case_insensitive(title, "Boost listingShare")
                cleaned_text = clean_title.replace("This listing is being reviewed.", "")
                cleaned_text = cleaned_text.replace("Tip: Renew your listing?", "")
                listings.append({
                    'title': cleaned_text,
                    'price': re.sub(r'[^\d]', '', price),
                    'date': date,
                    'status': status
                })
            break  # Stop on first successful match

    return listings

def get_count_of_elements_with_text(search_for, page):
    """Get count of elements with text"""
    length = len(get_elements_with_text(search_for, page))
    logging.info(f"length of elements with text: {length}")
    return length

def get_elements_with_text(search_for, page):
    """Get elements with text and enriched listing info"""
    locator = f"text={search_for}"
    elements = page.locator(locator).all()
    search_listings = []

    for el in elements:
        try:
            parent = el.evaluate_handle("node => node.closest('div[class*=\"x78zum5\"]')")
            if not parent:
                continue

            title_el = parent.query_selector('span[style*="-webkit-line-clamp: 2"]')
            if not title_el:
                title_el = parent.query_selector('span[style*="WebkitLineClamp: 2"]')
            logging.info(f"title_el: {title_el}")
            title = title_el.text_content().strip() if title_el else None

            price_el = parent.query_selector("span:has-text('AU$')")
            logging.info(f"price_el: {price_el}")
            if not price_el:
                price_el = parent.query_selector("span:has-text('A$')")
            price = "".join(filter(str.isdigit, price_el.inner_text())) if price_el else None

            if title and price and price_el:
                search_listings.append({
                    "title": title,
                    "price": price,
                    "element": el,
                    "price_element": price_el,
                    "status": None,
                    "date": None
                })

        except Exception as e:
            logging.error(f"Error parsing element: {e}")
            continue

    filter_listings_with_date = []
    try:
        sold_el = page.query_selector("span:has-text('Listed on')")
        if sold_el:
            logging.info(f"sold_el: {sold_el.text_content()}")
            filter_listings_with_date += extract_listings_with_status(sold_el.text_content())

    except Exception as e:
        logging.error(f"Error extracting listing metadata: {e}")

    # Corrected logging statement
    logging.info(f"filter_listings_with_date: {filter_listings_with_date}")
    logging.info(f"search_listings before matching the listing data: {search_listings}")

    # Match and enrich
    for search in search_listings:
        for record in filter_listings_with_date:
            logging.info(f"matched the listing data: {search['title']} {search['price']} {record['title']} {record['price']} {record['date']} {record['status']}")
            if (search['title'].lower() == record['title'].lower()
                and search['price'] == record['price']):
                search['date'] = record['date']
                search['status'] = record['status']
                break
    logging.info(f"search_listings: {search_listings}")
    return search_listings


def get_listing_image(page, alt_text: str = None):
    """
    Find the first listing image on the page.
    If alt_text is provided, match the <img> by its alt attribute.
    
    Returns:
        dict with {"image_element": Locator, "src": str} or None
    """
    try:
        if alt_text:
            # More precise: match by alt text
            img_loc = page.locator(f"img[alt='{alt_text}']").first
        else:
            # Fallback: match by stable classes inside listing container
            img_loc = page.locator(
                "div[aria-label] img.x15mokao.x1ga7v0g.x16uus16"
            ).first

        if img_loc.count() == 0:
            return None

        img_src = img_loc.get_attribute("src")
        return {"image_element": img_loc, "src": img_src}

    except Exception as e:
        logging.error(f"Failed to find listing image: {e}")
        return None


def perform_search_and_delete(search_for, listing_price, listing_date, session_cookie):
    """Perform search and delete listing with retry and timeout handling"""

    def handle_post_delete_flow(page, browser):
        try:
            not_answer_button = page.locator("//*[text()=\"I'd rather not answer\"]").first
            if not_answer_button and not_answer_button.is_visible():
                not_answer_button.click()
                random_sleep(settings.LONG_DELAY_START_TIME_BETWEEN_ELEMENTS_SELECTION, settings.LONG_DELAY_END_TIME_BETWEEN_ELEMENTS_SELECTION)
            else:
                logging.warning("'I'd rather not answer' button not found.")
                return 1, "'I'd rather not answer' button not found, but successfully deleted the product"

            next_button = page.locator("//*[text()='Next']").first
            if next_button and next_button.is_visible():
                next_button.click()
                random_sleep(settings.LONG_DELAY_START_TIME_BETWEEN_ELEMENTS_SELECTION, settings.LONG_DELAY_END_TIME_BETWEEN_ELEMENTS_SELECTION)
                logging.info("Process completed successfully.")
                return 1, "Successfully deleted the listing"
            else:
                logging.warning("'Next' button not found.")
                return 1, "'Next' button not found, but successfully deleted the product"
        finally:
            browser.close()

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True, args=["--start-maximized"])
            context = browser.new_context(storage_state=session_cookie, viewport={"width": 1920, "height": 1080})
            page = context.new_page()
            try:
                page.goto("https://www.facebook.com/marketplace/you/selling", timeout=30000)
            except Exception as e:
                logging.error(f"Timeout error navigating to Facebook Marketplace: {e}")
                browser.close()
                return 0, "Timeout error navigating to Facebook Marketplace"
            logging.info("Navigated to Facebook Marketplace vehicle listing page.")
            random_sleep(settings.DELAY_START_TIME_FOR_LOADING_PAGE, settings.DELAY_END_TIME_FOR_LOADING_PAGE)

            if not search_for.strip():
                browser.close()
                logging.info(f"Search value is required for {search_for}")
                return 4, "Search value is required"

            input_locator = "input[type='text'][placeholder='Search your listings'], input[type='text'][aria-label='Search your listings']"
            input_element = page.locator(input_locator).first

            if not input_element.is_visible():
                browser.close()
                logging.info(f"Search input not found for {search_for}")
                return 4, "Search input not found"

            input_element.click()
            input_element.fill(search_for)
            page.wait_for_timeout(3000)

            formatted_date = listing_date.strftime("%d/%m")
            matches_found = get_count_of_elements_with_text(search_for, page)

            if matches_found == 0:
                if page.locator("text='We didn't find anything'").is_visible():
                    logging.info("Detected 'We didn't find anything'")
                    browser.close()
                    return 6, "didnt_find_anything_displayed"
                else:
                    browser.close()
                    logging.info(f"No matching listing found for {search_for}")
                    return 4, "No matching listing found"

            logging.info(f"Found {matches_found} match(es) for '{search_for}'")
            elements = get_elements_with_text(search_for, page)
            logging.info(f"Dleting listings for {search_for} with price {listing_price} and date {listing_date} and formatted date {formatted_date}")

            for element in elements:
                try:
                    logging.info(f"Evaluating listing match for {element['title']} with price {element['price']} and date {element['date']}")
                    title_match = element['title'] and element['title'].lower() == search_for.lower()
                    price_match = element['price'] == "".join(filter(str.isdigit, listing_price))
                    date_match = element['date'] == formatted_date
                    logging.info(f"Title match: {title_match}, Price match: {price_match}, Date match: {date_match}")

                    if title_match and price_match and date_match:
                        # if status == "mark as sold" or status == "mark as available":
                        logging.info(f"Deleting listing: {element['title']} - {element['price']}")
                        result=get_listing_image(page, alt_text=f"{search_for}")
                        if result and result['image_element'] and result['image_element'].is_visible():
                            result["image_element"].click()
                            random_sleep(settings.LONG_DELAY_START_TIME_BETWEEN_ELEMENTS_SELECTION, settings.LONG_DELAY_END_TIME_BETWEEN_ELEMENTS_SELECTION)

                            success, message = find_and_click_delete_button(page)
                            if not success:
                                browser.close()
                                logging.info(f"Delete button not found for {search_for} and the message is {message}")
                                return 4, message

                            delete_buttons = page.locator("span.x1lliihq.x6ikm8r.x10wlt62.x1n2onr6.xlyipyv.xuxw1ft:has-text('Delete')").all()
                            if delete_buttons:
                                target_button = delete_buttons[len(delete_buttons)-1]
                                if target_button.is_visible():
                                    target_button.click()
                                    random_sleep(settings.LONG_DELAY_START_TIME_BETWEEN_ELEMENTS_SELECTION, settings.LONG_DELAY_END_TIME_BETWEEN_ELEMENTS_SELECTION)
                                    return handle_post_delete_flow(page, browser)
                            #2nd Attempt
                            delete_buttons = page.locator("span:text('Delete')").all()
                            if delete_buttons:
                                logging.info(f"2nd attempt: delete_buttons: {delete_buttons} and length of delete_buttons: {len(delete_buttons)}")
                                target_button = delete_buttons[len(delete_buttons)-1]
                                if target_button.is_visible():
                                    target_button.click()
                                    random_sleep(settings.LONG_DELAY_START_TIME_BETWEEN_ELEMENTS_SELECTION, settings.LONG_DELAY_END_TIME_BETWEEN_ELEMENTS_SELECTION)
                                    return handle_post_delete_flow(page, browser)
                                
                            logging.error("Delete button not found or not visible.")
                            browser.close()
                            return 4, "Delete button not found"
                        elif element['price_element'] and element['price_element'].is_visible():
                            element['price_element'].click()
                            random_sleep(settings.LONG_DELAY_START_TIME_BETWEEN_ELEMENTS_SELECTION, settings.LONG_DELAY_END_TIME_BETWEEN_ELEMENTS_SELECTION)

                            success, message = find_and_click_delete_button(page)
                            if not success:
                                browser.close()
                                logging.info(f"Delete button not found for {search_for} and the message is {message}")
                                return 4, message

                            delete_buttons = page.locator("span.x1lliihq.x6ikm8r.x10wlt62.x1n2onr6.xlyipyv.xuxw1ft:has-text('Delete')").all()
                            if delete_buttons:
                                target_button = delete_buttons[len(delete_buttons)-1]
                                if target_button.is_visible():
                                    target_button.click()
                                    random_sleep(settings.LONG_DELAY_START_TIME_BETWEEN_ELEMENTS_SELECTION, settings.LONG_DELAY_END_TIME_BETWEEN_ELEMENTS_SELECTION)
                                    return handle_post_delete_flow(page, browser)
                            #2nd Attempt
                            delete_buttons = page.locator("span:text('Delete')").all()
                            if delete_buttons:
                                logging.info(f"2nd attempt: delete_buttons: {delete_buttons} and length of delete_buttons: {len(delete_buttons)}")
                                target_button = delete_buttons[len(delete_buttons)-1]
                                if target_button.is_visible():
                                    target_button.click()
                                    random_sleep(settings.LONG_DELAY_START_TIME_BETWEEN_ELEMENTS_SELECTION, settings.LONG_DELAY_END_TIME_BETWEEN_ELEMENTS_SELECTION)
                                    return handle_post_delete_flow(page, browser)
                                
                            logging.error("Delete button not found or not visible.")
                            browser.close()
                            return 4, "Delete button not found"
                        else:
                            browser.close()
                            logging.info(f"Price element not found or not visible for {search_for}")
                            return 4, "Price element not found or not visible"
                except Exception as e:
                    logging.error(f"Error evaluating listing match: {e}")
                    continue

            browser.close()
            logging.info(f"No matching listing found for {search_for}")
            return 4, "No matching listing found"

    except Exception as e:
        logging.error(f"Unhandled error in perform_search_and_delete: {e}")
        if 'browser' in locals():
            browser.close()
        return 4, str(e)



def find_and_click_delete_button(page):
    """Find and click the 'Delete' button"""
    logging.info("Attempting to find the 'Delete' button...")
    # Different XPath variants to locate the Delete button
    delete_selectors = [
        "//div[@aria-label='Delete' and @role='button']",
        "//span[contains(text(), 'Delete')]/ancestor::div[@role='button']", 
        "//span[contains(text(), 'Delete')]/parent::span/parent::div", 
    ]

    for selector in delete_selectors:
        try:
            delete_buttons = page.query_selector_all(selector)
            if delete_buttons:
                for button in delete_buttons:
                    if button.is_visible():
                        button.click()
                        page.wait_for_timeout(2000)
                        logging.info(f"Successfully clicked the 'Delete' button using selector: {selector}")
                        return True, "Successfully deleted the listing"
                logging.warning(f"Elements found for selector {selector}, but none were visible.")
        except Exception as e:
            logging.error(f"Error while searching with selector {selector}: {e}")

    logging.error("Delete button not found using any selector.")
    return False, "Failed to delete the listing"


def get_facebook_profile_listings(profile_url,session_cookie):
    """Get all listings from any Facebook Marketplace profile URL."""
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True,args=["--start-maximized"])
            context = browser.new_context(storage_state=session_cookie, viewport={"width": 1920, "height": 1080})
            page = context.new_page()
            # Set shorter timeout for navigation
            page.set_default_timeout(20000)
            # Navigate to profile URL
            page.goto(profile_url)
            page.wait_for_timeout(1000)
            # More specific selectors for profile listings
            listing_selectors = [
                f'div[style*="max-width: 175px"] a[href*="/marketplace/item/"]',
            ]
            # First hover over the listings container
            listings_container = page.locator('div[style*="max-width: 175px"]').first
            listings_container.hover()
            max_count = 3 
            start_time = time.time()
            max_time = 90

            # Use a set to store unique href links
            href_links = set()

            # Use a dictionary to store unique listings with details
            unique_profile_listings = {}

            while max_count and (time.time() - start_time) < max_time:
                # Enhanced scroll sequence
                for _ in range(4):  # Increased to 4 scrolls per iteration
                    # Multiple scroll methods
                    page.keyboard.press("PageDown")
                    page.wait_for_timeout(400)

                    page.evaluate("window.scrollBy(0, 1000)")
                    page.wait_for_timeout(400)

                    page.evaluate("""
                        window.scrollTo(0, document.body.scrollHeight);
                        window.scrollTo(0, document.body.scrollHeight + 1500);
                    """)
                    page.wait_for_timeout(500)

                    # Try to scroll the last element into view
                    try:
                        for selector in listing_selectors:
                            elements = page.query_selector_all(selector)
                            if elements:
                                elements[-1].scroll_into_view_if_needed()
                                break
                    except Exception:
                        continue

                # Collect href links and listing details in this iteration
                for selector in listing_selectors:
                    elements = page.query_selector_all(selector)
                    for element in elements:
                        try:
                            href = element.get_attribute('href')
                            if href and '/marketplace/item/' in href:
                                href_links.add(href)

                                # Extract details for the dictionary
                                listing_id = href.split('/item/')[1].split('/')[0]
                                title_element = element.query_selector('span[style*="-webkit-line-clamp: 2"]')
                                price_element = element.query_selector('span:has-text("$")')
                                location_element = element.query_selector('span[class*="xlyipyv"]')
                                mileage_element = element.query_selector('span[class*="x1lliihq"]:has-text("km")')
                                title = title_element.text_content() if title_element else None
                                price = "".join(filter(str.isdigit, price_element.text_content())) if price_element else None
                                location = location_element.text_content() if location_element else None
                                mileage = "".join(filter(str.isdigit, mileage_element.text_content())) if mileage_element else None
                                if mileage:
                                    mileage = int(mileage) * 1000
                                    mileage=str(mileage)


                                # Add to the dictionary
                                unique_profile_listings[listing_id] = {
                                    'title': title,
                                    'id': listing_id,
                                    'price': price,
                                    'location': location,
                                    'mileage': mileage,
                                    'url': f"https://www.facebook.com/marketplace/item/{listing_id}/"
                                }

                        except Exception as e:
                            logging.error(f"Error extracting href or details: {str(e)}")
                            continue
                max_count-=1
                logging.info(f"Current href links collected: {len(href_links)}")
                logging.info(f" Current listings collected in dictionary: {len(unique_profile_listings)}")

            browser.close()
            logging.info(f"Successfully extracted {len(unique_profile_listings)} unique listings")
            return True, unique_profile_listings

    except Exception as e:
        logging.error(f"Error in get_profile_listings: {e}")
        browser.close()
        return False, str(e)

def is_convertible_to_int(value):
    """
    Checks if the given value can be converted to an integer.
    """
    try:
        int(value)
        return True
    except (ValueError, TypeError):
        return False

def random_delay(min_time=settings.SHORT_DELAY_START_TIME_BETWEEN_ELEMENTS_SELECTION, max_time=settings.SHORT_DELAY_END_TIME_BETWEEN_ELEMENTS_SELECTION):
    """
    Adds a randomized delay to mimic human behavior.
    """
    time.sleep(random.uniform(min_time, max_time))

def extract_facebook_listing_details(current_listing, session):
    """
    Extract details of a Facebook Marketplace listing.
    """
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(storage_state=session)
        page = context.new_page()

        try:
            logging.info(f"Navigating to listing URL: {current_listing['url']}")
            page.goto(current_listing['url'], timeout=60000)

            # Mimic human behavior
            random_delay(settings.LONG_DELAY_START_TIME_BETWEEN_ELEMENTS_SELECTION, settings.LONG_DELAY_END_TIME_BETWEEN_ELEMENTS_SELECTION)

            listing = {
                "url": current_listing['url'],
                "year": None,
                "make": None,
                "model": None,
                "price": None,
                "mileage": current_listing["mileage"],
                "description": "No Description Provided",
                "images": [],
                "location": None,
                "transmission": "Automatic transmission",
                "condition": "Excellent",
                "fuel_type": "Other",
                "driven": None,
                "exterior_colour": "Other",
                "interior_colour": "Other",
            }

            # Extract each part of the listing
            extract_price(page, listing)
            random_delay()
            # extract_mileage(page, listing)
            # random_delay()
            extract_year_make_model(page, listing)
            random_delay()
            extract_about_the_vehicle(page, listing)
            random_delay()
            extract_description(page, listing)
            random_delay()

            listing["images"] = extract_images(page)
            random_delay()
            listing["location"] = extract_location(page)

            logging.info(f"Successfully extracted details for listing: {current_listing['url']}")  
            browser.close()          
            return listing

        except PlaywrightTimeoutError:
            logging.error("Page loading timeout occurred.")
            browser.close()
            return None
        except Exception as e:
            logging.error(f"Error extracting listing details: {e}")
            browser.close()
            return None

def extract_price(page, listing):
    """
    Extracts the price from the listing.
    """
    try:
        element = page.query_selector("//h1[@dir]/../following-sibling::div[1]//span")
        if element:
            text = element.inner_text()
            logging.info(f"Price text: {text}")
            if text:
                listing["price"] = "".join(filter(str.isdigit, text))
    except Exception as e:
        logging.error(f"Error extracting price: {e}")

def extract_year_make_model(page, listing):
    """
    Extracts the year, make, and model of the vehicle.
    """
    try:
        elements = page.query_selector_all("//h1//span[@dir='auto']")
        for element in elements:
            text = element.inner_text().strip()
            parts = text.split()
            if len(parts) >= 2:
                try:
                    if is_convertible_to_int(parts[0]):
                        year = int(parts[0])
                        if 1900 <= year <= 2025:
                            listing["year"] = year
                            listing["make"] = parts[1]
                            listing["model"] = " ".join(parts[2:])
                            return
                    else:
                        listing["year"] = 2020
                        listing["make"] = parts[0]
                        listing["model"] = " ".join(parts[1:])
                except ValueError:
                   logging.warning(f"Failed to extract year from text: {text}")
            else:
                logging.warning("No valid year, make, and model found.")
    except Exception as e:
        logging.error(f"Error extracting year, make, and model: {e}")

def extract_description(page, listing):
    """
    Extracts the description from the listing.
    """
    try:
        see_more_button = page.query_selector("//*[text()='See more']")
        if see_more_button:
            see_more_button.click()
            random_delay(settings.SHORT_DELAY_START_TIME_BETWEEN_ELEMENTS_SELECTION, settings.SHORT_DELAY_END_TIME_BETWEEN_ELEMENTS_SELECTION)

        description_element = page.query_selector("//*[text()='See less']/../..")
        if description_element:
            text = description_element.inner_text()
            listing["description"] = text.replace("See less", "").strip()
    except Exception as e:
        logging.error(f"Error extracting description: {e}")

def extract_images(page):
    """
    Extracts image URLs from the listing.
    """
    try:
        image_urls = []
        elements = page.query_selector_all("//*[starts-with(@aria-label, 'Thumbnail')]//img")
        for el in elements:
            src = el.get_attribute("src")
            if src:
                image_urls.append(src)
        return image_urls
    except Exception as e:
        logging.error(f"Error extracting images: {e}")
        return []

def extract_location(page):
    """
    Extracts the location from the listing.
    """
    try:
        element = page.query_selector("//a[contains(@href, '/marketplace/')]/span")
        if element:
            return element.inner_text().split(',')[0].strip()
    except Exception as e:
        logging.error(f"Error extracting location: {e}")
    return None


def extract_about_the_vehicle(page, listing):
    """
    Extracts the 'About the vehicle' section from the listing.
    """
    try:
        # Check for the 'About this vehicle' header
        about_header = page.query_selector("//h2[contains(@class, 'xdj266r') and contains(., 'About this vehicle')]")
        if about_header:
            logging.info("'About this vehicle' section found.")

            # Extract 'Driven' information
            driven_element = page.query_selector("//span[contains(@class, 'x193iq5w') and contains(., 'Driven')]")
            if driven_element:
                driven_text = driven_element.inner_text()
                logging.info(f"Driven info: {driven_text}")
                mileage_text = "".join(filter(str.isdigit, driven_text))
                listing['driven'] = mileage_text

            # Extract 'Exterior and Interior colour' information
            colour_element = page.query_selector("//span[contains(@class, 'x193iq5w') and contains(., 'Exterior colour')]")
            if colour_element:
                colour_text = colour_element.inner_text()
                logging.info(f"Colour info: {colour_text}")
                exterior_colour = re.search(r'Exterior colour: (\w+)', colour_text)
                interior_colour = re.search(r'Interior colour: (\w+)', colour_text)
                listing['exterior_colour'] = exterior_colour.group(1) if exterior_colour else "Other"
                listing['interior_colour'] = interior_colour.group(1) if interior_colour else "Other"

            # Extract 'Fuel type' information
            fuel_type_element = page.query_selector("//span[contains(@class, 'x193iq5w') and contains(., 'Fuel type')]")
            if fuel_type_element:
                fuel_type_text = fuel_type_element.inner_text()
                logging.info(f"Fuel type info: {fuel_type_text}")
                fuel_type = fuel_type_text.split("Fuel type:")[1].strip()
                listing['fuel_type'] = fuel_type

            # Extract 'Condition' information
            condition_element = page.query_selector("//span[contains(@class, 'x193iq5w') and contains(., 'condition')]")
            if condition_element:
                condition_text = condition_element.inner_text()
                logging.info(f"Condition info: {condition_text}")
                condition=condition_text.split()[0]
                listing['condition'] = condition
            # Extract 'Transmission' information
            transmission_element = page.query_selector("//span[contains(@class, 'x193iq5w') and contains(., 'transmission')]")
            if transmission_element:
                transmission_text = transmission_element.inner_text()
                logging.info(f"Transmission info: {transmission_text}")
                listing['transmission'] = transmission_text
                print(transmission_text)

    except Exception as e:
        logging.error(f"Error extracting 'About the vehicle': {e}")



def verify_facebook_listing_images_upload(search_for, listing_price, listing_date, session_cookie):
    """Perform search and delete listing if image is not uploaded with retry and timeout handling"""
    logging.info(f"Verifying images upload status for the listing: {search_for} and price: {listing_price} and date: {listing_date}")
    def handle_post_delete_flow(page, browser):
        try:
            not_answer_button = page.locator("//*[text()=\"I'd rather not answer\"]").first
            if not_answer_button and not_answer_button.is_visible():
                not_answer_button.click()
                random_sleep(settings.LONG_DELAY_START_TIME_BETWEEN_ELEMENTS_SELECTION, settings.LONG_DELAY_END_TIME_BETWEEN_ELEMENTS_SELECTION)
            else:
                logging.warning("'I'd rather not answer' button not found.")
                return 0, "'I'd rather not answer' button not found, but successfully deleted the product"

            next_button = page.locator("//*[text()='Next']").first
            if next_button and next_button.is_visible():
                next_button.click()
                random_sleep(settings.LONG_DELAY_START_TIME_BETWEEN_ELEMENTS_SELECTION, settings.LONG_DELAY_END_TIME_BETWEEN_ELEMENTS_SELECTION)
                logging.info("Process completed successfully.")
                return 0, "Successfully deleted the listing"
            else:
                logging.warning("'Next' button not found.")
                return 0, "'Next' button not found, but successfully deleted the product"
        finally:
            browser.close()

    try:
        
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True, args=["--start-maximized"])
            context = browser.new_context(storage_state=session_cookie, viewport={"width": 1920, "height": 1080})
            page = context.new_page()
            try:
                page.goto("https://www.facebook.com/marketplace/you/selling", timeout=30000)
            except Exception as e:
                logging.error(f"Timeout error navigating to Facebook Marketplace: {e}")
                browser.close()
                return 5, "Timeout error navigating to Facebook Marketplace"
            logging.info("Navigated to Facebook Marketplace vehicle listing page.")
            random_sleep(settings.DELAY_START_TIME_FOR_LOADING_PAGE, settings.DELAY_END_TIME_FOR_LOADING_PAGE)

            if not search_for.strip():
                browser.close()
                return 4, "Search value is required"

            input_locator = "input[type='text'][placeholder='Search your listings'], input[type='text'][aria-label='Search your listings']"
            input_element = page.locator(input_locator).first

            if not input_element.is_visible():
                browser.close()
                return 4, "Search input not found"

            input_element.click()
            input_element.fill(search_for)
            page.wait_for_timeout(3000)

            formatted_date = listing_date.strftime("%d/%m")
            matches_found = get_count_of_elements_with_text(search_for, page)

            if matches_found == 0:
                if page.locator("text='We didn't find anything'").is_visible():
                    logging.info("Detected 'We didn't find anything'")
                    browser.close()
                    return 6, "didnt_find_anything_displayed"
                else:
                    browser.close()
                    return 4, "No matching listing found"

            logging.info(f"Found {matches_found} match(es) for '{search_for}'")
            elements = get_elements_with_text(search_for, page)

            for element in elements:
                try:
                    title_match = element['title'] and element['title'].lower() == search_for.lower()
                    logging.info(f"both titles are {element['title']} and {search_for}")
                    listing_price=str(listing_price)
                    logging.info(f"{element["price"]} and {listing_price} and type of listing_price: {type(listing_price)} and type of element['price']: {type(element['price'])}")
                    price_match = element['price'] == "".join(filter(str.isdigit, listing_price))

                    date_match = element['date'] == formatted_date
                    logging.info(f"both dates are {element['date']} and  from database{formatted_date}")
                    
                    # status = element.get('status', '').lower()
                    logging.info(f"title_match: {title_match} and price_match: {price_match} and date_match: {date_match}")

                    if title_match and price_match and date_match:
                        # if status == "mark as sold" or status == "mark as available":
                        logging.info(f"Deleting listing: {element['title']} - {element['price']}")
                        result=get_listing_image(page, alt_text=f"{search_for}")
                        if result and result['image_element'] and result['image_element'].is_visible():
                            result["image_element"].click()
                            random_sleep(settings.LONG_DELAY_START_TIME_BETWEEN_ELEMENTS_SELECTION, settings.LONG_DELAY_END_TIME_BETWEEN_ELEMENTS_SELECTION)
                            if is_image_uploaded(page):
                                browser.close()
                                return 1, "Image is uploaded and visible"
                            else:
                                logging.info("Image is not uploaded")

                                success, message = find_and_click_delete_button(page)
                                if not success:
                                    browser.close()
                                    return 4, message
                                #first Attempt
                                delete_buttons = page.locator("span.x1lliihq.x6ikm8r.x10wlt62.x1n2onr6.xlyipyv.xuxw1ft:has-text('Delete')").all()
                                if delete_buttons:
                                    logging.info(f"first attempt: delete_buttons: {delete_buttons} and length of delete_buttons: {len(delete_buttons)}")
                                    
                                    target_button = delete_buttons[len(delete_buttons)-1]
                                    if target_button.is_visible():
                                        target_button.click()
                                        random_sleep(settings.LONG_DELAY_START_TIME_BETWEEN_ELEMENTS_SELECTION, settings.LONG_DELAY_END_TIME_BETWEEN_ELEMENTS_SELECTION)
                                        return handle_post_delete_flow(page, browser)

                                #2nd Attempt
                                delete_buttons = page.locator("span:text('Delete')").all()
                                if delete_buttons:
                                    logging.info(f"2nd attempt: delete_buttons: {delete_buttons} and length of delete_buttons: {len(delete_buttons)}")
                                    target_button = delete_buttons[len(delete_buttons)-1]
                                    if target_button.is_visible():
                                        target_button.click()
                                        random_sleep(settings.LONG_DELAY_START_TIME_BETWEEN_ELEMENTS_SELECTION, settings.LONG_DELAY_END_TIME_BETWEEN_ELEMENTS_SELECTION)
                                        return handle_post_delete_flow(page, browser)
                                
                                logging.error("Delete button not found or not visible.")
                                browser.close()
                                return 4, "Delete button not found"
                        
                        elif element['price_element'] and element['price_element'].is_visible():
                            element['price_element'].click()
                            random_sleep(settings.LONG_DELAY_START_TIME_BETWEEN_ELEMENTS_SELECTION, settings.LONG_DELAY_END_TIME_BETWEEN_ELEMENTS_SELECTION)

                            if is_image_uploaded(page):
                                browser.close()
                                return 1, "Image is uploaded and visible"
                            else:
                                logging.info("Image is not uploaded")

                                success, message = find_and_click_delete_button(page)
                                if not success:
                                    browser.close()
                                    return 4, message
                                #first Attempt
                                delete_buttons = page.locator("span.x1lliihq.x6ikm8r.x10wlt62.x1n2onr6.xlyipyv.xuxw1ft:has-text('Delete')").all()
                                if delete_buttons:
                                    logging.info(f"first attempt: delete_buttons: {delete_buttons} and length of delete_buttons: {len(delete_buttons)}")
                                    
                                    target_button = delete_buttons[len(delete_buttons)-1]
                                    if target_button.is_visible():
                                        target_button.click()
                                        random_sleep(settings.LONG_DELAY_START_TIME_BETWEEN_ELEMENTS_SELECTION, settings.LONG_DELAY_END_TIME_BETWEEN_ELEMENTS_SELECTION)
                                        return handle_post_delete_flow(page, browser)

                                #2nd Attempt
                                delete_buttons = page.locator("span:text('Delete')").all()
                                if delete_buttons:
                                    logging.info(f"2nd attempt: delete_buttons: {delete_buttons} and length of delete_buttons: {len(delete_buttons)}")
                                    target_button = delete_buttons[len(delete_buttons)-1]
                                    if target_button.is_visible():
                                        target_button.click()
                                        random_sleep(settings.LONG_DELAY_START_TIME_BETWEEN_ELEMENTS_SELECTION, settings.LONG_DELAY_END_TIME_BETWEEN_ELEMENTS_SELECTION)
                                        return handle_post_delete_flow(page, browser)
                                
                                logging.error("Delete button not found or not visible.")
                                browser.close()
                                return 4, "Delete button not found"
                        
                        else:
                            browser.close()
                            return 4, "Price element not found or not visible"
                    else:
                        logging.info(f"Listing does not match: {element['title']} - {element['price']} - {element['date']}")
                        continue
                except Exception as e:
                    logging.error(f"Error evaluating listing match: {e}")
                    continue

            browser.close()
            return 4, "No matching listing found"

    except Exception as e:
        logging.error(f"Unhandled error in perform_search_and_delete: {e}")
        if 'browser' in locals():
            browser.close()
        return 4, str(e)

def is_image_uploaded(page):
    """
    Check if an image is uploaded by verifying the presence of the image element.
    """
    try:
        # Define the selector for the image element
        image_selector = "div.xpyat2d.x1exxlbk img[src^='https://scontent']"
        
        # Wait for the image element to be visible
        logging.info("Waiting for image element to be visible...")
        image_element = page.wait_for_selector(image_selector, timeout=10000, state="visible")
        
        # Check if the image element is visible
        if image_element:
            # Get the image src attribute
            image_src = image_element.get_attribute("src")
            logging.info(f"Image is uploaded and visible. Image URL: {image_src}")
            return True
        else:
            logging.info("Image is not uploaded.")
            return False
    except PlaywrightTimeoutError:
        logging.warning("Timeout: Image element not found within 10 seconds.")
        return False
    except Exception as e:
        logging.error(f"Error checking image upload status: {e}")
        return False



def extract_restricted_listing_details(page, title):
    """
    Extract details from a specific listing by its title and check for action required.
    Returns a dictionary with listing details if found and requires action.
    """
    try:
        # Find all elements with matching aria-label
        listing_elements = page.query_selector_all(f'div[aria-label="{title}"]')
        logging.info(f"Found {len(listing_elements)} elements with title: {title}")
        listings=[]
        
        for listing_el in listing_elements:
            # Check if this listing requires action
            action_text = listing_el.query_selector('div.x1f6kntn.x117nqv4.xcly8g5')
            if action_text and ("Please take action on this listing" in action_text.text_content() or "It looks like you've created a duplicate listing." in action_text.text_content()):
                logging.info(f"action_text: {action_text.text_content()}")
                # Extract price
                price_el = listing_el.query_selector('span[dir="auto"]:has-text("AU$")')
                price = price_el.text_content().replace('AU$', '').strip() if price_el else None
                if not price_el:
                    price_el = listing_el.query_selector('span[dir="auto"]:has-text("A$")')
                    price = price_el.text_content().replace('A$', '').strip() if price_el else None
                if not price_el:
                    price_el = listing_el.query_selector('span[dir="auto"]:has-text("PKR")')
                    price = price_el.text_content().replace('PKR', '').strip() if price_el else None
                
                # Extract listed date
                date_el = listing_el.query_selector('span:has-text("Listed on")')
                listed_date = None
                if date_el:
                    date_text = date_el.text_content()
                    date_match = re.search(r'Listed on (\d{1,2}/\d{1,2})', date_text)
                    if date_match:
                        listed_date = date_match.group(1)
                
                listings.append({
                    'title': title,
                    'price': price,
                    'listed_date': listed_date,
                    'requires_action': True,
                    'element': listing_el  # Return the element for further use if needed
                })
            else:
                action_text = listing_el.query_selector('div.x1f6kntn.x117nqv4.x1a1m0xk')
                if action_text and ("Please take action on this listing" in action_text.text_content() or "It looks like you've created a duplicate listing." in action_text.text_content()):
                    logging.info(f"action_text: {action_text.text_content()}")
                    # Extract price
                    price_el = listing_el.query_selector('span[dir="auto"]:has-text("AU$")')
                    price = price_el.text_content().replace('AU$', '').strip() if price_el else None
                    if not price_el:
                        price_el = listing_el.query_selector('span[dir="auto"]:has-text("A$")')
                        price = price_el.text_content().replace('A$', '').strip() if price_el else None
                    if not price_el:
                        price_el = listing_el.query_selector('span[dir="auto"]:has-text("PKR")')
                        price = price_el.text_content().replace('PKR', '').strip() if price_el else None
                    
                    # Extract listed date
                    date_el = listing_el.query_selector('span:has-text("Listed on")')
                    listed_date = None
                    if date_el:
                        date_text = date_el.text_content()
                        date_match = re.search(r'Listed on (\d{1,2}/\d{1,2})', date_text)
                        if date_match:
                            listed_date = date_match.group(1)
                    
                    listings.append({
                        'title': title,
                        'price': price,
                        'listed_date': listed_date,
                        'requires_action': True,
                        'element': listing_el  # Return the element for further use if needed
                    })

        return listings
        
    except Exception as e:
        logging.error(f"Error extracting listing details: {e}")
        return None

def check_restricted_listing_images(page, listings):
    """
    Check for images in each listing and add image URL to the listing details.
    Returns updated listings with image information.
    """
    try:
        for listing in listings:
            try:
                # Get the element from the listing
                element = listing['element']
                
                # Click on the listing to open details
                element.click()
                random_sleep(settings.LONG_DELAY_START_TIME_BETWEEN_ELEMENTS_SELECTION, settings.LONG_DELAY_END_TIME_BETWEEN_ELEMENTS_SELECTION)  # Wait for details to load
                
                # Check for image using the specific selector
                image_el = page.query_selector('div.xpyat2d.x1exxlbk img')
                
                if image_el:
                    random_sleep(settings.LONG_DELAY_START_TIME_BETWEEN_ELEMENTS_SELECTION, settings.LONG_DELAY_END_TIME_BETWEEN_ELEMENTS_SELECTION)
                    # Get image URL from src attribute
                    image_url = image_el.get_attribute('src')
                    listing['image_url'] = image_url
                    logging.info(f"Found image for listing: {listing['title']}")
                else:
                    listing['image_url'] = None
                    logging.info(f"No image found for listing: {listing['title']}")
                
                # Go back to listings page
                page.go_back()
                random_sleep(settings.LONG_DELAY_START_TIME_BETWEEN_ELEMENTS_SELECTION, settings.LONG_DELAY_END_TIME_BETWEEN_ELEMENTS_SELECTION)  # Wait for page to load
                
            except Exception as e:
                logging.error(f"Error processing listing {listing['title']}: {e}")
                listing['image_url'] = None
                continue
        
        return listings
        
    except Exception as e:
        logging.error(f"Error in check_listing_images: {e}")
        return listings
    

def delete_restricted_listings_without_images(page, listing):
    """
    Delete listings that have no images (image_url is None).
    Returns True if all deletions were successful, False otherwise.
    """
    try:
        
        if listing.get('image_url') is None:
            logging.info(f"Processing deletion for restricted listing: {listing['title']}")
                
            result=get_listing_image(page, alt_text=f"{listing['title']}")
                        
            if result and result['image_element'] and result['image_element'].is_visible():
                result["image_element"].click()
                random_sleep(settings.LONG_DELAY_START_TIME_BETWEEN_ELEMENTS_SELECTION, settings.LONG_DELAY_END_TIME_BETWEEN_ELEMENTS_SELECTION)
                
                # First attempt with find_and_click_delete_button
                success, message = find_and_click_delete_button(page)
                if not success:
                    logging.error(f"Failed to delete restricted listing {listing['title']}: {message}")
                    return False, "Failed to delete the restricted listing"
                    
                # First Attempt with specific selector
                delete_buttons = page.locator("span.x1lliihq.x6ikm8r.x10wlt62.x1n2onr6.xlyipyv.xuxw1ft:has-text('Delete')").all()
                if delete_buttons:
                    logging.info(f"First attempt: Found {len(delete_buttons)} delete buttons")
                    target_button = delete_buttons[len(delete_buttons)-1]
                    if target_button.is_visible():
                        target_button.click()
                        logging.info(f"target_button: {target_button} is clicked successfully")
                        random_sleep(settings.LONG_DELAY_START_TIME_BETWEEN_ELEMENTS_SELECTION, settings.LONG_DELAY_END_TIME_BETWEEN_ELEMENTS_SELECTION)
                        return True, "Successfully deleted the restricted listing"
                    
                # Second Attempt with alternative selector
                delete_buttons = page.locator("span:text('Delete')").all()
                if delete_buttons:
                    logging.info(f"Second attempt: Found {len(delete_buttons)} delete buttons")
                    target_button = delete_buttons[len(delete_buttons)-1]
                    if target_button.is_visible():
                        target_button.click()
                        random_sleep(settings.LONG_DELAY_START_TIME_BETWEEN_ELEMENTS_SELECTION, settings.LONG_DELAY_END_TIME_BETWEEN_ELEMENTS_SELECTION)
                        return True, "Successfully deleted the restricted listing"   

                else:
                    logging.error("Delete button not found or not visible.")
                    return False, "Delete button not found"
            elif listing['element'] and listing['element'].is_visible():
                listing['element'].click()
                random_sleep(settings.LONG_DELAY_START_TIME_BETWEEN_ELEMENTS_SELECTION, settings.LONG_DELAY_END_TIME_BETWEEN_ELEMENTS_SELECTION)

                # First attempt with find_and_click_delete_button
                success, message = find_and_click_delete_button(page)
                if not success:
                    logging.error(f"Failed to delete restricted listing {listing['title']}: {message}")
                    return False, "Failed to delete the restricted listing"
                    
                # First Attempt with specific selector
                delete_buttons = page.locator("span.x1lliihq.x6ikm8r.x10wlt62.x1n2onr6.xlyipyv.xuxw1ft:has-text('Delete')").all()
                if delete_buttons:
                    logging.info(f"First attempt: Found {len(delete_buttons)} delete buttons")
                    target_button = delete_buttons[len(delete_buttons)-1]
                    if target_button.is_visible():
                        target_button.click()
                        logging.info(f"target_button: {target_button} is clicked successfully")
                        random_sleep(settings.LONG_DELAY_START_TIME_BETWEEN_ELEMENTS_SELECTION, settings.LONG_DELAY_END_TIME_BETWEEN_ELEMENTS_SELECTION)
                        return True, "Successfully deleted the restricted listing"
                    
                # Second Attempt with alternative selector
                delete_buttons = page.locator("span:text('Delete')").all()
                if delete_buttons:
                    logging.info(f"Second attempt: Found {len(delete_buttons)} delete buttons")
                    target_button = delete_buttons[len(delete_buttons)-1]
                    if target_button.is_visible():
                        target_button.click()
                        random_sleep(settings.LONG_DELAY_START_TIME_BETWEEN_ELEMENTS_SELECTION, settings.LONG_DELAY_END_TIME_BETWEEN_ELEMENTS_SELECTION)
                        return True, "Successfully deleted the restricted listing"   

                else:
                    logging.error("Delete button not found or not visible.")
                    return False, "Delete button not found"
            else:
                logging.error(f"Image element not found for restricted listing {listing['title']}")
                return False, "Image element not found for restricted listing"
        
    except Exception as e:
        logging.error(f"Error in delete_restricted_listings_without_images: {e}")
        return False, "Failed to delete the restricted listing"

def verify_restricted_listing_matches(listings, title, price, listing_date):
    """
    Verify listings against exact matches of title, price, and listing date.
    Returns filtered list of matching listings.
    """
    try:
        # Clean and normalize inputs
        if price.startswith('AU$'):
            clean_price = str(price).replace('AU$', '').replace(',', '').strip()
        
        elif price.startswith('PKR'):
            clean_price = str(price).replace('PKR', '').replace(',', '').strip()
        
        elif price.startswith('A$'):
            clean_price = str(price).replace('A$', '').replace(',', '').strip()
        else:
            clean_price = str(price)
        clean_date = listing_date.strip()

        
        # Filter listings that match all criteria
        logging.info(f"extract_restricted_listing_details: {listings} and details from database title: {title} and price: {clean_price} and listing_date: {clean_date}")
        matching_listings = []  
        for listing in listings:
            # Clean listing price for comparison
            if listing['price'] and listing['price'].startswith('AU$'):
                listing_price = str(listing['price']).replace('AU$', '').replace(',', '').strip()
            elif listing['price'] and listing['price'].startswith('PKR'):
                listing_price = str(listing['price']).replace('PKR', '').replace(',', '').strip()
            elif listing['price'] and listing['price'].startswith('A$'):
                listing_price = str(listing['price']).replace('A$', '').replace(',', '').strip()
            else:
                listing_price = str(listing['price']).replace(',', '').strip()
            logging.info(f"listing['listed_date']: {listing['listed_date']}")
            # Check for exact matches
            if (listing['title'] == title and 
                listing_price == clean_price and 
                listing['listed_date'] == clean_date):
                matching_listings.append(listing)
                logging.info(f"Found matching listing: {listing['title']}")
            else:
                logging.info(f"Listing {listing['title']} did not match criteria")
        
        logging.info(f"Found {len(matching_listings)} matching listings out of {len(listings)} total listings")
        return matching_listings
        
    except Exception as e:
        logging.error(f"Error in verify_listing_matches: {e}")
        return []

def filter_restricted_listings_without_valid_images(listings):
    """
    Filter listings that have no image URL or image URL doesn't start with 'https://scontent'.
    Returns filtered list of listings.
    """
    try:
        filtered_listings = []
        for listing in listings:
            image_url = listing.get('image_url')
            
            # Check if image_url is None or doesn't start with 'https://scontent'
            if image_url is None or not str(image_url).startswith('https://scontent'):
                filtered_listings.append(listing)
                logging.info(f"Restricted listing {listing['title']} has no valid image URL")
            else:
                logging.info(f"Restricted listing {listing['title']} has valid image URL")
        
        logging.info(f"Found {len(filtered_listings)} restricted listings without valid images out of {len(listings)} total restricted listings")
        return filtered_listings
        
    except Exception as e:
        logging.error(f"Error in filter_listings_without_valid_images: {e}")
        return filtered_listings

def verify_image_upload_restricted_listings(page,listing_title,listing_price,listing_date):
    """
    Verify and process Facebook Marketplace listings with restricted image uploads.
    This function:
    1. Extracts listings with specific title
    2. Verifies exact matches for title, price, and date
    3. Checks for valid image uploads
    4. Filters listings without valid images
    5. Processes deletion for listings without valid images
    """
    try:                
                # Navigate to Facebook Marketplace
                logging.info("Navigating to Facebook Marketplace...")
                try:
                    page.goto("https://www.facebook.com/marketplace/you/selling",wait_until="networkidle",timeout=30000)
                except Exception as e:
                    logging.error(f"Timeout error navigating to Facebook Marketplace: {e}")
                    return 6, "Timeout error navigating to Facebook Marketplace"
                # Add initial delay after page load
                random_sleep(2, 4)

                # Extract listings with specific title
                logging.info(f"Searching for restricted listings with title: {listing_title}")
                listing_details = extract_restricted_listing_details(page, listing_title)
                
                if not listing_details:
                    logging.warning("No restricted listings found with the specified title")
                    return 2, "No restricted listings found with the specified title"
                
                logging.info(f"Found {len(listing_details)} initial restricted listings")
                # Verify listings with exact matches
                verified_listings = verify_restricted_listing_matches(
                    listing_details,
                    title=listing_title,
                    price=listing_price,
                    listing_date=listing_date
                )
                
                if not verified_listings:
                    logging.warning("No verified restricted listings found matching the criteria")
                    return 2, "No verified restricted listings found matching the criteria"
                
                logging.info(f"Found {len(verified_listings)} verified restricted listings matching criteria")
                
                # Check for images in verified listings
                logging.info("Checking for images in verified restricted listings...")
                updated_listings = check_restricted_listing_images(page, verified_listings)
                random_sleep(2, 4)
                
                logging.info(f"Found {len(updated_listings)} restricted listings with image information")
                
                # Filter listings without valid images
                logging.info("Filtering restricted listings without valid images...")
                listings_to_delete = filter_restricted_listings_without_valid_images(updated_listings)
                
                if not listings_to_delete:
                    logging.info("No restricted listings found that need to be deleted")
                    return 1, "No restricted listings found that need to be deleted"
                
                logging.info(f"Found {len(listings_to_delete)} restricted listings to delete")
                
                # Process deletion for the first listing
                target_listing = listings_to_delete[0]
                logging.info(f"Processing deletion for restricted listing: {target_listing['title']}")
                random_sleep(2, 4)
                
                deletion_success, message = delete_restricted_listings_without_images(page, target_listing)
                
                if deletion_success:
                    logging.info(f"Successfully deleted restricted listing: {target_listing['title']}")
                    logging.info(f"Message: {message}")
                    return 0, "Successfully deleted the restricted listing"
                else:
                    logging.warning(f"Failed to delete restricted listing: {target_listing['title']} but image is not uploaded and message: {message}")
                    return 4, "Failed to delete the restricted listing"
                
    except Exception as e:
        logging.error(f"Error in verify_image_upload_restricted_listings: {str(e)}")
        return 2, "Error in verify_image_upload_restricted_listings"


def handle_post_delete_flow(page, browser):
        try:
            not_answer_button = page.locator("//*[text()=\"I'd rather not answer\"]").first
            if not_answer_button and not_answer_button.is_visible():
                not_answer_button.click()
                random_sleep(settings.LONG_DELAY_START_TIME_BETWEEN_ELEMENTS_SELECTION, settings.LONG_DELAY_END_TIME_BETWEEN_ELEMENTS_SELECTION)
            else:
                logging.warning("'I'd rather not answer' button not found.")
                return 0, "'I'd rather not answer' button not found, but successfully deleted the product"

            next_button = page.locator("//*[text()='Next']").first
            if next_button and next_button.is_visible():
                next_button.click()
                random_sleep(settings.LONG_DELAY_START_TIME_BETWEEN_ELEMENTS_SELECTION, settings.LONG_DELAY_END_TIME_BETWEEN_ELEMENTS_SELECTION)
                logging.info("Process completed successfully.")
                return 0, "Successfully deleted the listing"
            else:
                logging.warning("'Next' button not found.")
                return 0, "'Next' button not found, but successfully deleted the product"
        except Exception as e:
            logging.error(f"Error in handle_post_delete_flow: {e}")
            return 0, "Error in handle_post_delete_flow"

def image_upload_verification_with_search(page,browser,search_for, listing_price, listing_date):
    """Perform search and delete listing if image is not uploaded with retry and timeout handling"""

    try:
        try:
            page.goto("https://www.facebook.com/marketplace/you/selling", timeout=30000)
        except Exception as e:
            logging.error(f"Timeout error navigating to Facebook Marketplace: {e}")
            return 6, "Timeout error navigating to Facebook Marketplace"
        logging.info("Navigated to Facebook Marketplace vehicle listing page.")
        random_sleep(settings.DELAY_START_TIME_FOR_LOADING_PAGE, settings.DELAY_END_TIME_FOR_LOADING_PAGE)

        input_locator = "input[type='text'][placeholder='Search your listings'], input[type='text'][aria-label='Search your listings']"
        input_element = page.locator(input_locator).first

        if not input_element.is_visible():
            return 5, "Search input not found"

        input_element.click()
        input_element.fill(search_for)
        page.wait_for_timeout(3000)

        matches_found = get_count_of_elements_with_text(search_for, page)

        if matches_found == 0:
            if page.locator("text='We didn't find anything'").is_visible():
                logging.info("Detected 'We didn't find anything'")
                return 7, "didnt_find_anything_displayed"
            else:
                logging.info(f"No matching listing found for {search_for} ")
                return 2, "No matching listing found"

        logging.info(f"Found {matches_found} match(es) for '{search_for}'")
        elements = get_elements_with_text(search_for, page)

        for element in elements:
            try:
                title_match = element['title'] and element['title'].lower() == search_for.lower()
                logging.info(f"both titles are {element['title']} and {search_for}")
                listing_price=str(listing_price)
                logging.info(f"{element["price"]} and {listing_price} and type of listing_price: {type(listing_price)} and type of element['price']: {type(element['price'])}")
                price_match = element['price'] == "".join(filter(str.isdigit, listing_price))

                date_match = element['date'] == listing_date
                logging.info(f"both dates are {element['date']} and  from database{listing_date}")
                # status = element.get('status', '').lower()
                logging.info(f"title_match: {title_match} and price_match: {price_match} and date_match: {date_match}")

                if title_match and price_match and date_match:
                    # if status == "mark as sold" or status == "mark as available":
                    logging.info(f"Deleting listing: {element['title']} - {element['price']}")
                    result=get_listing_image(page, alt_text=f"{search_for}")
                    if result and result['image_element'] and result['image_element'].is_visible():
                        result["image_element"].click()
                        random_sleep(settings.LONG_DELAY_START_TIME_BETWEEN_ELEMENTS_SELECTION, settings.LONG_DELAY_END_TIME_BETWEEN_ELEMENTS_SELECTION)
                        if is_image_uploaded(page):
                            logging.info("Image is uploaded and visible")
                            return 1, "Image is uploaded and visible"
                        else:
                            logging.info("Image is not uploaded")

                            success, message = find_and_click_delete_button(page)
                            if not success:
                                return 4, message
                            #first Attempt
                            delete_buttons = page.locator("span.x1lliihq.x6ikm8r.x10wlt62.x1n2onr6.xlyipyv.xuxw1ft:has-text('Delete')").all()
                            if delete_buttons:
                                logging.info(f"first attempt: delete_buttons: {delete_buttons} and length of delete_buttons: {len(delete_buttons)}")
                                    
                                target_button = delete_buttons[len(delete_buttons)-1]
                                if target_button.is_visible():
                                    target_button.click()
                                    random_sleep(settings.LONG_DELAY_START_TIME_BETWEEN_ELEMENTS_SELECTION, settings.LONG_DELAY_END_TIME_BETWEEN_ELEMENTS_SELECTION)
                                    return handle_post_delete_flow(page, browser)

                            #2nd Attempt
                            delete_buttons = page.locator("span:text('Delete')").all()
                            if delete_buttons:
                                logging.info(f"2nd attempt: delete_buttons: {delete_buttons} and length of delete_buttons: {len(delete_buttons)}")
                                target_button = delete_buttons[len(delete_buttons)-1]
                                if target_button.is_visible():
                                    target_button.click()
                                    random_sleep(settings.LONG_DELAY_START_TIME_BETWEEN_ELEMENTS_SELECTION, settings.LONG_DELAY_END_TIME_BETWEEN_ELEMENTS_SELECTION)
                                    return handle_post_delete_flow(page, browser)
                                
                            logging.error("Delete button not found or not visible.")
                            return 4, "Delete button not found"
                        
                    elif element['price_element'] and element['price_element'].is_visible():
                        element["price_element"].click()
                        random_sleep(settings.LONG_DELAY_START_TIME_BETWEEN_ELEMENTS_SELECTION, settings.LONG_DELAY_END_TIME_BETWEEN_ELEMENTS_SELECTION)
                        if is_image_uploaded(page):
                            logging.info("Image is uploaded and visible")
                            return 1, "Image is uploaded and visible"
                        else:
                            logging.info("Image is not uploaded")

                            success, message = find_and_click_delete_button(page)
                            if not success:
                                return 4, message
                            #first Attempt
                            delete_buttons = page.locator("span.x1lliihq.x6ikm8r.x10wlt62.x1n2onr6.xlyipyv.xuxw1ft:has-text('Delete')").all()
                            if delete_buttons:
                                logging.info(f"first attempt: delete_buttons: {delete_buttons} and length of delete_buttons: {len(delete_buttons)}")
                                    
                                target_button = delete_buttons[len(delete_buttons)-1]
                                if target_button.is_visible():
                                    target_button.click()
                                    random_sleep(settings.LONG_DELAY_START_TIME_BETWEEN_ELEMENTS_SELECTION, settings.LONG_DELAY_END_TIME_BETWEEN_ELEMENTS_SELECTION)
                                    return handle_post_delete_flow(page, browser)

                            #2nd Attempt
                            delete_buttons = page.locator("span:text('Delete')").all()
                            if delete_buttons:
                                logging.info(f"2nd attempt: delete_buttons: {delete_buttons} and length of delete_buttons: {len(delete_buttons)}")
                                target_button = delete_buttons[len(delete_buttons)-1]
                                if target_button.is_visible():
                                    target_button.click()
                                    random_sleep(settings.LONG_DELAY_START_TIME_BETWEEN_ELEMENTS_SELECTION, settings.LONG_DELAY_END_TIME_BETWEEN_ELEMENTS_SELECTION)
                                    return handle_post_delete_flow(page, browser)
                                
                            logging.error("Delete button not found or not visible.")
                            return 4, "Delete button not found"
                    else:
                        return 4, "Image element not found for edit/delete the listings"

                else:
                    logging.info(f"Listing does not match: {element['title']} - {element['price']} - {element['date']}")
                    continue
            except Exception as e:
                logging.error(f"Error evaluating listing match: {e}")
                continue
        logging.info("No matching listing found")
        return 2, "No matching listing found"

    except Exception as e:
        logging.error(f"Unhandled error in image_upload_verification_with_search: {e}")
        return 0, "Unhandled error in image_upload_verification_with_search"


def image_upload_verification(relisting,vehicle_listing):
    """Verify image upload"""
    if relisting:
        search_title = f"{relisting.listing.year} {relisting.listing.make} {relisting.listing.model}"
        search_price = str(relisting.listing.price)
        search_date = timezone.localtime(relisting.relisting_date).strftime("%d/%m")
        status=relisting.status
    else:
        search_title = f"{vehicle_listing.year} {vehicle_listing.make} {vehicle_listing.model}"
        search_price = str(vehicle_listing.price)
        search_date = timezone.localtime(vehicle_listing.listed_on).strftime("%d/%m")
        status=vehicle_listing.status
    if status == "completed":
        logging.info(f"Vehicle listing {search_title} is created successfully and Now verifying image upload")
        logging.info(f"search_title: {search_title} and search_price: {search_price} and search_date: {search_date}")
        user = relisting.user if relisting else vehicle_listing.user
        credentials = FacebookUserCredentials.objects.filter(user=user).first()
        try:
            with sync_playwright() as p:
                # Initialize browser with proper configuration
                logging.info("Initializing browser...")
                browser = p.chromium.launch(
                    headless=True,
                    args=["--start-maximized"]
                )
                context = browser.new_context(
                    viewport={'width': 1920, 'height': 1080},
                    storage_state=credentials.session_cookie
                )
                page = context.new_page()
                try:
                    result=verify_image_upload_restricted_listings(page,search_title,search_price,search_date)
                    if result[0] == 1:
                        logging.info(f"Restricted listing {search_title} has image uploaded successfully")
                        browser.close()
                        logging.info("Browser closed successfully")
                        return 1, "Successfully verified image upload"
                    elif result[0] == 0:
                        logging.info(f"Restricted listing {search_title} has no image uploaded and deleted successfully")
                        browser.close()
                        logging.info("Browser closed successfully")
                        return 0, "Restricted listing has no image uploaded and deleted successfully"
                    elif result[0] == 4:
                        logging.info(f"Restricted listing {search_title} has no image uploaded and failed to delete")
                        logging.info(f"Error: {result[1]}")
                        browser.close()
                        logging.info("Browser closed successfully")
                        return 4, "Restricted listing has no image uploaded and failed to delete"
                    else:
                        logging.error(f"Error in image_upload_verification: {result[1]}")
                        logging.info("Listing not found in restricted listings. Retrying with search...")
                        page = context.new_page()
                        result=image_upload_verification_with_search(page,browser,search_title, search_price, search_date)
                        if result[0] == 0:
                            logging.info(f"Approved listing {search_title} has no image uploaded and deleted successfully")
                            browser.close()
                            logging.info("Browser closed successfully")
                            return 0, "Approved listing has no image uploaded and deleted successfully"
                        elif result[0] == 1:
                            logging.info(f"Approved listing {search_title} has image uploaded successfully")
                            browser.close()
                            logging.info("Browser closed successfully")
                            return 1, "Successfully verified image upload"
                        elif result[0] == 2:
                            logging.info(f"Approved listing {search_title} not found, Retrying daily one time")
                            browser.close()
                            logging.info("Browser closed successfully")
                            return 2, "Approved listing not found, Retrying daily one time"
                        elif result[0] == 4:
                            logging.info(f"Approved listing {search_title} has no image uploaded and failed to delete.. Retry attempt daily one time")
                            browser.close()
                            logging.info("Browser closed successfully")
                            return 4, "Approved listing has no image uploaded and failed to delete"
                        elif result[0] == 6:
                            logging.info(f"Failed to load the page")
                            browser.close()
                            logging.info("Browser closed successfully")
                            handle_retry_or_disable_credentials(credentials, user)
                            return 6, "Failed to load the page"
                        elif result[0] == 7:
                            logging.info(f"No matching listing found for the user {user.email} and listing title {search_title}")
                            logging.info(f"response[1]: {result[1]}")
                            return 7, "No matching listing found for the user {user.email} and listing title {search_title}"
                        else:
                            logging.info("failed to verify image upload")
                            logging.info(f"Error: {result[1]}")
                            browser.close()
                            logging.info("Browser closed successfully")
                            return 5, "failed to verify image upload"
                except Exception as e:
                    logging.error(f"Error in image_upload_verification: {e}")
                    browser.close()
                    logging.info("Browser closed successfully")
                    return 5,"Error in image_upload_verification"

        except Exception as e:
            logging.error(f"Error in image_upload_verification: {e}")
            return 5,"Error in image_upload_verification"
    else:
        logging.info(f"Vehicle listing {search_title} is not completed")
        return 5, "Vehicle listing is not completed"


def perform_search_and_extract_listings(search_title, search_price, listed_on, session_cookies):
    """
    Optimized function to search for listings and extract details.
    
    Args:
        search_title (str): Title to search for
        search_price (str): Price to match
        listed_on (datetime): Listing date
        session_cookies (str): Session cookies for authentication
    
    Returns:
        tuple: (status_code, message)
            0 = timeout, 1 = success, 2 = not_found, 3 = error, 4 = failed
    """
    # Validate inputs
    if not search_title or not search_price or not session_cookies:
        logging.error("Missing required parameters")
        return 4, "Missing required parameters"
    
    if not listed_on:
        logging.error(f"Listing date not set for: {search_title}")
        return 2, "Listing date not set"
    
    # Format date
    try:
        formatted_date = timezone.localtime(listed_on).strftime("%d/%m")
    except Exception as e:
        logging.error(f"Date formatting error: {e}")
        return 4, "Date formatting error"
    
    browser = None
    try:
        with sync_playwright() as p:
            # Initialize browser
            browser = p.chromium.launch(
                headless=True,
                args=["--start-maximized"]
            )
            
            context = browser.new_context(
                viewport={'width': 1920, 'height': 1080},
                storage_state=session_cookies
            )
            
            page = context.new_page()
            
            # Navigate to marketplace
            try:
                page.goto(
                    "https://www.facebook.com/marketplace/you/selling",
                    wait_until="networkidle",
                    timeout=30000
                )
            except Exception as e:
                logging.error(f"Navigation timeout: {e}")
                browser.close()
                return 0, "Navigation timeout"
            
            random_sleep(2, 4)
            
            # Perform search
            search_input = page.locator(
                "input[type='text'][placeholder='Search your listings'], "
                "input[type='text'][aria-label='Search your listings']"
            ).first
            
            if not search_input.is_visible(timeout=5000):
                logging.error("Search input not found")
                browser.close()
                return 4, "Search input not found"
            
            search_input.click()
            search_input.fill(search_title)
            page.wait_for_timeout(3000)
            
            # Check for matches
            matches_count = get_count_of_elements_with_text(search_title, page)
            
            if matches_count == 0:
                if page.locator("text='We didn't find anything'").is_visible():
                    logging.info("No results found message displayed")
                    browser.close()
                    return 2, "No results found"
                logging.info(f"No matches for: {search_title}")
                browser.close()
                return 4, "No matching listings"
            
            logging.info(f"Found {matches_count} matches for: {search_title}")
            
            # Extract and verify listings
            elements = get_elements_with_text(search_title, page)
            price_digits = "".join(filter(str.isdigit, search_price))
            
            for element in elements:
                if (element.get('title', '').lower() == search_title.lower() and
                    element.get('price') == price_digits and
                    element.get('date') == formatted_date):
                    
                    logging.info(f"Found exact match: {element['title']} - {element['price']} - {element['date']}")
                    browser.close()
                    return 1, "Matching listing found"
            
            logging.info("No exact matches found")
            browser.close()
            return 2, "No exact matches"
            
    except Exception as e:
        logging.error(f"Search operation failed: {e}")
        return 4, f"Search operation failed: {str(e)}"
    
    finally:
        if browser:
            try:
                browser.close()
            except Exception as e:
                logging.warning(f"Browser cleanup error: {e}")

def search_facebook_listing_sync(credential, listings, relistings):
    """
    Synchronous version of search_facebook_listing to avoid async context issues.
    Uses threading to isolate from Django's async context detection.
    
    Args:
        credential: Facebook user credentials
        listings: List of vehicle listings to search for
        relistings: List of relistings to search for
    
    Returns:
        None
    """
    from concurrent.futures import ThreadPoolExecutor
    
    def _playwright_worker():
        """Worker function that runs in a separate thread to avoid async context issues"""
        try:
            items = list(listings) if listings else []
            items.extend(list(relistings) if relistings else [])
            
            if not items:
                logging.info(f"No items to process for user {credential.user.email}")
                return None
            
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True, args=["--start-maximized"])
                context = browser.new_context(
                    viewport={'width': 1920, 'height': 1080},
                    storage_state=credential.session_cookie
                )
                page = context.new_page()
                
                try:
                    page.goto("https://www.facebook.com/marketplace/you/selling", 
                            wait_until="networkidle", timeout=30000)
                    random_sleep(2, 3)
                    
                    while items:
                        item = items.pop(0)
                        if not should_delete_listing(item.user):
                            logging.info(f"5-minute cooldown for user {item.user.email}")
                            items.append(item)  # Re-queue for later
                            continue
                            
                        # Extract item details
                        if hasattr(item, 'listing'):
                            title = f"{item.listing.year} {item.listing.make} {item.listing.model}"
                            price = str(item.listing.price)
                            date = timezone.localtime(item.relisting_date).strftime("%d/%m")
                        else:
                            title = f"{item.year} {item.make} {item.model}"
                            price = str(item.price)
                            date = timezone.localtime(item.listed_on).strftime("%d/%m")
                
                        # Find search input
                        search_input = page.locator(
                            "input[type='text'][placeholder='Search your listings'], "
                            "input[type='text'][aria-label='Search your listings']"
                        ).first
                        
                        if not search_input.is_visible(timeout=5000):
                            logging.error(f"Listing {title}: Search input not found")
                            continue
                        
                        # Perform search
                        logging.info(f"Searching for '{title}'")
                        search_input.click()
                        search_input.clear()
                        search_input.fill(title)
                        page.wait_for_timeout(3000)
                        
                        # Check for matches
                        matches_count = get_count_of_elements_with_text(title, page)
                        logging.info(f"Listing {title}: Found {matches_count} potential matches")
                        
                        if matches_count == 0:
                            if page.locator("text='We didn't find anything'").is_visible():
                                logging.info(f"Listing {title}: No results found (Facebook message displayed)")
                            else:
                                logging.info(f"No matches found for {title}")
                            continue
                        
                        # Extract and verify listings
                        elements = get_elements_with_text(title, page)
                        price_digits = "".join(filter(str.isdigit, price))
                        
                        logging.info(f"Listing {title}: Extracted {len(elements)} elements for verification")
                        for element in elements:
                            try:
                                element_title = element.get('title', '').strip()
                                element_price = str(element.get('price', '')).strip()
                                element_date = element.get('date', '')
                                
                                # Match criteria
                                title_match = element_title.lower() == title.lower()
                                price_match = element_price == price_digits
                                date_match = element_date == date
                                
                                if title_match and price_match and date_match:
                                    logging.info(f"listing {title} already exist, not update the listed date")
                                    logging.info(f"Listing {title}: Found exact match - Title: '{element_title}', Price: ${element_price}, Date: {element_date}")
                                    continue
                                elif title_match and price_match:
                                    logging.info(f"listing {title} already exist, updating the listed date")
                                    logging.info(f"Listing {title}: Found match - Title: '{element_title}', Price: ${element_price}, Date: {element_date}")
                                    if hasattr(item, 'listing'):
                                        day, month = map(int, element_date.split('/'))
                                        item.relisting_date = item.relisting_date.replace(day=day, month=month)
                                        item.status="completed"
                                        item.listing.save()
                                        logging.info(f"Listing {title}: Updated the listed date")
                                        credential.user.last_listing_time = timezone.now()
                                        credential.user.save()
                                    else:
                                        day, month = map(int, element_date.split('/'))
                                        item.listed_on = item.listed_on.replace(day=day, month=month)
                                        item.status="completed"
                                        item.save()
                                        logging.info(f"Listing {title}: Updated the listed date")
                                        credential.user.last_listing_time = timezone.now()
                                        credential.user.save()
                                else:
                                    credential.user.last_listing_time = timezone.now()
                                    credential.user.save()
                                    logging.debug(f"Listing {title}: No match - Title match: {title_match}, Price match: {price_match}")
                                    logging.info(f"Retry attempt for searching the listing in marketplace")
                                    if hasattr(item, 'listing'):
                                        if item.listing.retry_count < settings.MAX_RETRIES_ATTEMPTS:
                                            item.listing.retry_count += 1
                                            item.listing.save()
                                            items.append(item)
                                        else:
                                            item.status = "duplicate"
                                            item.save()
                                            logging.error(f"Max retries reached for {title}, marking as duplicate")
                                    else:
                                        if item.retry_count < settings.MAX_RETRIES_ATTEMPTS:
                                            item.retry_count += 1
                                            item.save()
                                            items.append(item)
                                        else:
                                            item.status = "action_needed"
                                            item.save()
                                            logging.error(f"Max retries reached for {title}, marking as action_needed")
                                    continue
                                    
                            except Exception as e:
                                logging.error(f"Listing {title}: Error processing element: {e}")
                                continue
                        
                        logging.info(f"Listing {title}: not found in marketplace, retrying with search")
                        continue
                        
                finally:
                    if browser:
                        try:
                            browser.close()
                        except Exception as e:
                            logging.warning(f"Error closing browser: {e}")
                    
        except Exception as e:
            logging.error(f"Error processing listing for user '{credential.user.email}': {e}")
            raise
    
    # Run in a separate thread to completely isolate from Django's async context
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(_playwright_worker)
        try:
            return future.result(timeout=300)  # 5 minute timeout
        except Exception as e:
            logging.error(f"ThreadPoolExecutor error: {e}")
            raise

def find_and_delete_duplicate_listing_sync(credential, listings, relistings):
    """
    Synchronous version of find_and_delete_duplicate_listing to avoid async context issues.
    Uses threading to isolate from Django's async context detection.
    
    Args:
        credential: Facebook user credentials
        listings: List of vehicle listings to process
        relistings: List of relistings to process
    
    Returns:
        tuple: (success_count, error_count)
    """
    from concurrent.futures import ThreadPoolExecutor
    
    def _playwright_worker():
        """Worker function that runs in a separate thread to avoid async context issues"""
        try:
            items = list(listings) if listings else []
            items.extend(list(relistings) if relistings else [])
            
            if not items:
                logging.info(f"No items to process for user {credential.user.email}")
                return 0, 0
            
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True, args=["--start-maximized"])
                context = browser.new_context(
                    viewport={'width': 1920, 'height': 1080},
                    storage_state=credential.session_cookie
                )
                page = context.new_page()
                
                try:
                    page.goto("https://www.facebook.com/marketplace/you/selling", 
                            wait_until="networkidle", timeout=30000)
                    random_sleep(2, 3)
                    
                    deleted_count = 0
                    error_count = 0
                    
                    while items:
                        item = items.pop(0)
                        if not should_delete_listing(item.user):
                            logging.info(f"5-minute cooldown for user {item.user.email}")
                            items.append(item)  # Re-queue for later
                            continue
                            
                        try:
                            # Extract item details
                            if hasattr(item, 'listing'):
                                title = f"{item.listing.year} {item.listing.make} {item.listing.model}"
                                price = str(item.listing.price)
                                date = timezone.localtime(item.relisting_date).strftime("%d/%m")
                            else:
                                title = f"{item.year} {item.make} {item.model}"
                                price = str(item.price)
                                date = timezone.localtime(item.listed_on).strftime("%d/%m")
                            
                            logging.info(f"Processing duplicate deletion for: {title}")
                            
                            # Find and delete restricted listings
                            restricted_listings = extract_restricted_listing_details(page, title)
                            if not restricted_listings:
                                logging.info(f"No duplicate listings found for {title}, skipping")
                                continue
                            
                            verified_listings = verify_restricted_listing_matches(
                                restricted_listings, title=title, price=price, listing_date=date
                            )
                            
                            if verified_listings:
                                target = verified_listings[0]
                                if not target.get('image_url'):
                                    logging.info(f"Listing {title} has no image URL, preparing for deletion")
                                    target['image_url'] = None
                                    
                                success, message = delete_restricted_listings_without_images(page, target)
                                if success:
                                    # Update item status
                                    if hasattr(item, 'listing'):
                                        item.listing.status = "deleted"
                                        item.listing.retry_count = 0
                                        item.listing.save()
                                    else:
                                        item.status = "deleted"
                                        item.retry_count = 0
                                        item.save()
                                    
                                    # Update user timestamp
                                    item.user.last_delete_listing_time = timezone.now()
                                    item.user.save()
                                    
                                    deleted_count += 1
                                    logging.info(f"Successfully deleted duplicate listing: {title}")
                                else:
                                    logging.warning(f"Failed to delete duplicate: {title} - {message}")
                                    error_count += 1
                                    
                                    # Update user timestamp even on failure to respect rate limiting
                                    item.user.last_delete_listing_time = timezone.now()
                                    item.user.save()
                                    
                                    # Handle retry logic
                                    if hasattr(item, 'listing'):
                                        if item.listing.retry_count < settings.MAX_RETRIES_ATTEMPTS:
                                            item.listing.retry_count += 1
                                            item.listing.save()
                                            items.append(item)
                                        else:
                                            item.listing.status = "failed_deletion"
                                            item.listing.save()
                                            logging.error(f"Max retries reached for {title}, marking as failed_deletion")
                                    else:
                                        if item.retry_count < settings.MAX_RETRIES_ATTEMPTS:
                                            item.retry_count += 1
                                            item.save()
                                            items.append(item)
                                        else:
                                            item.status = "failed_deletion"
                                            item.save()
                                            logging.error(f"Max retries reached for {title}, marking as failed_deletion")
                            else:
                                logging.info(f"No verified listings found for {title}, skipping duplicate listing deletion")
                                continue
                            
                            # Small delay between operations
                            random_sleep(1, 2)
                        
                        except Exception as e:
                            logging.error(f"Error processing {title}: {e}")
                            error_count += 1
                            continue
                    
                    logging.info(f"Duplicate deletion completed. Deleted: {deleted_count}, Errors: {error_count}")
                    return deleted_count, error_count
                        
                finally:
                    if browser:
                        try:
                            browser.close()
                        except Exception as e:
                            logging.warning(f"Error closing browser: {e}")
                    
        except Exception as e:
            logging.error(f"Error in duplicate deletion for user '{credential.user.email}': {e}")
            raise
    
    # Run in a separate thread to completely isolate from Django's async context
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(_playwright_worker)
        try:
            return future.result(timeout=600)  # 10 minute timeout for deletion operations
        except Exception as e:
            logging.error(f"ThreadPoolExecutor error in duplicate deletion: {e}")
            raise
