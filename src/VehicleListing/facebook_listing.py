import logging
import random
import time
from playwright.sync_api import sync_playwright
import os
import requests
from fastapi import HTTPException

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

images_folder = os.path.join(os.path.dirname(__file__), '..', 'static', 'images')

def human_like_typing(element, text):
    """Simulate human-like typing with random delays."""
    for char in text:
        element.type(char, delay=random.uniform(50, 100))  
        time.sleep(random.uniform(0.05, 0.1))

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
            suggestion = page.locator(f"//div[@role='option' or @role='listbox']//span[contains(text(), '{value}')]").first
            suggestion.click()
        except:
            input_element.press("Enter")

    if use_tab:
        input_element.press("Tab")

    random_sleep(1, 2)  # Random delay after filling the field
    return True

def select_dropdown_option(page, field_name, option_text):
    """Helper function to select dropdown options with random delays."""
    logging.info(f"Selecting {field_name}...")
    try:
        dropdown = page.locator(f"//label[@aria-label='{field_name}' and @role='combobox']").first
        dropdown.scroll_into_view_if_needed()
        dropdown.click()
        random_sleep(0.5, 1)  # Random delay after clicking the dropdown

        try:
            option = page.locator(f"//div[@role='option' or @role='listbox']//span[contains(text(), '{option_text}')]").first
            option.scroll_into_view_if_needed()
            option.click()
        except:
            dropdown.fill(option_text)
            dropdown.press("Enter")

        logging.info(f"{field_name} selected successfully.")
        random_sleep(1, 2)  # Random delay after selecting the option
        return True
    except Exception as e:
        logging.error(f"Error selecting {field_name}: {e}")
        raise

def select_vehicle_type(page):
    """Select the vehicle type (Car/Truck) with random delays."""
    logging.info("Selecting vehicle type...")
    try:
        vehicle_dropdown = page.locator("//span[contains(text(), 'Vehicle type')]/ancestor::label").first
        vehicle_dropdown.scroll_into_view_if_needed()
        vehicle_dropdown.click()
        random_sleep(0.5, 1)  # Random delay after clicking the dropdown

        car_option = page.locator("//div[@role='option'][contains(.,'Car/Truck')]").first
        car_option.scroll_into_view_if_needed()
        car_option.click()

        logging.info("Vehicle type (Car/Truck) selected successfully.")
        random_sleep(1, 2)  # Random delay after selecting the option
        return True
    except Exception as e:
        logging.error(f"Error selecting vehicle type: {e}")
        raise

def create_marketplace_listing(listing,session_cookie):
    """Create a new listing on Facebook Marketplace with human-like interactions."""
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=False)
            context = browser.new_context(storage_state=session_cookie)
            page = context.new_page()

            # Navigate to the vehicle listing page
            page.goto("https://www.facebook.com/marketplace/create/vehicle", timeout=60000)
            logging.info("Navigated to Facebook Marketplace vehicle listing page.")
            random_sleep(2, 3)  # Random delay after page load

            # Vehicle details
            vehicle_details = {
                "Year": listing.year,
                "Make": listing.make,
                "Model": listing.model,
                "Price": str(listing.price),
                "Location": listing.location,
                "Mileage": str(listing.mileage),
                "Description": listing.description
            }

            # Select vehicle type
            select_vehicle_type(page)

            # Download the image and save it locally    
            image_name = os.path.basename(listing.images)
            image_extension = os.path.splitext(image_name)[1] 
            new_image_name = f"{listing.list_id}_image{image_extension}"  
            local_image_path = os.path.join(images_folder, new_image_name)

            try:
                # Download the image
                image_response = requests.get(listing.images)
                image_response.raise_for_status()
                with open(local_image_path, "wb") as file:
                    file.write(image_response.content)
            except requests.exceptions.RequestException as e:
                raise HTTPException(status_code=500, detail=f"Error downloading the image: {e}")
                        # Check if the image was downloaded successfully
            if not os.path.exists(local_image_path):
                raise Exception("Image download failed, file does not exist.")

            # Upload images
            image_input = page.locator("//input[@type='file']").first
            image_input.set_input_files(local_image_path)
            logging.info("Photos uploaded successfully.")
            random_sleep(2, 3)  # Random delay after uploading images

            # Fill form fields
            select_dropdown_option(page, "Year", vehicle_details["Year"])

            # Input fields with their selectors
            input_fields = {
                "Make": [
                    # "//input[@id=':r31:']",
                    "//span[contains(text(), 'Make')]/following-sibling::input",
                    "//div[contains(@class, 'xjbqb8w')]//input[contains(@class, 'x1i10hfl')]"
                ],
                "Model": [
                    # "//input[@id=':r34:']",
                    "//span[contains(text(), 'Model')]/following-sibling::input",
                    "//div[contains(@class, 'xjbqb8w')]//input[contains(@class, 'x1i10hfl')]"
                ],
                "Mileage": [
                    # "//input[@id=':r3f:']",
                    "//span[contains(text(), 'Mileage')]/following-sibling::input",
                    "//div[contains(@class, 'xjbqb8w')]//input[contains(@class, 'x1i10hfl')]"
                ],
                "Price": [
                    # "//input[@id=':r37:']",
                    "//label[@aria-label='Price']//input",
                    "//span[contains(text(), 'Price')]/following-sibling::input"
                ],
                "Location": [
                    # "//input[@id=':r3o:']",
                    "//label[@aria-label='Location']//input",
                    "//input[@role='combobox' and @aria-label='Location']"
                ],
                "Description": [
                    # "//textarea[@id=':r49:']",
                    "//textarea[contains(@class, 'x1i10hfl')]",
                    "//span[contains(text(), 'Description')]/following-sibling::div//textarea"
                ]
            }

            for field, selectors in input_fields.items():
                fill_input_field(
                    page,
                    field,
                    vehicle_details[field],
                    selectors,
                    use_suggestion=(field in ["Make", "Model", "Location"]),
                    use_tab=(field in ["Mileage", "Price", "Description"])
                )

            # Select dropdowns
            select_dropdown_option(page, "Body style", "Sedan")
            select_dropdown_option(page, "Fuel type", listing.fuel_type)
            select_dropdown_option(page, "Vehicle condition", "Excellent")

            #            # Select dropdowns
            # if listing.body_style not in ["Coupe", "Sedan", "SUV", "Truck","Hatchback","Convertible","Minivan","Small Car", "Wagon"]:
            #     select_dropdown_option(page, "Body style", "Other")
            # else:
            #     select_dropdown_option(page, "Body style", listing.body_style)
            
            # if listing.fuel_type not in ["Petrol - Premium Unleaded", "Petrol - Regular Unleaded", "Diesel","Gasoline","Flex","Plug-in Hybrid","Electric", "Hybrid"]:
            #     select_dropdown_option(page, "Fuel type", "Other")
            # else:
            #     select_dropdown_option(page, "Fuel type", listing.fuel_type)
            
            # if listing.vehicle_condition not in ["Excellent", "Good", "Fair", "Poor"]:
            #     select_dropdown_option(page, "Vehicle condition", "Other")
            # else:
            #     select_dropdown_option(page, "Vehicle condition", listing.vehicle_condition)
            
            # if listing.transmission not in ["Automatic Transmission", "Manual Transmission"]:
            #     select_dropdown_option(page, "Transmission", "Other")
            # else:
            #     select_dropdown_option(page, "Transmission", listing.transmission)

            # Submit form
            for button_text in ["Next", "Publish"]:
                try:
                    button = page.locator(
                        f"//div[@aria-label='{button_text}' and @role='button']" +
                        f"|//span[contains(text(), '{button_text}')]/ancestor::div[@role='button']"
                    ).first
                    button.scroll_into_view_if_needed()
                    button.click()
                    logging.info(f"Clicked {button_text} button.")
                    random_sleep(3, 5)  # Random delay after clicking the button
                except Exception as e:
                    logging.error(f"Failed to click {button_text} button: {e}")
                    raise

            # Close browser
            browser.close()
            logging.info("Browser closed successfully.")
            os.remove(local_image_path)
            logging.info("Image file deleted successfully.")
            return True, "Listing created successfully"

    except Exception as e:
        logging.error(f"Error in create_marketplace_listing: {e}")
        return False, str(e)
def login_to_facebook( email, password,session_cookie=None):
    """Log in to Facebook automatically."""
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=False)
            context = browser.new_context()
            page = context.new_page()
            # Navigate to Facebook login page
            page.goto("https://www.facebook.com/login", timeout=10000)
            logging.info("Navigated to Facebook login page.")
            random_sleep(2, 3)  # Random delay after page load

            # Handle cookie consent
            handle_cookie_consent(page)

            # Fill email field
            email_field = page.locator('input[name="email"]').first
            email_field.scroll_into_view_if_needed()
            human_like_typing(email_field, email)
            logging.info("Email filled successfully.")

            # Fill password field
            password_field = page.locator('input[name="pass"]').first
            password_field.scroll_into_view_if_needed()
            human_like_typing(password_field, password)
            logging.info("Password filled successfully.")

            # Click login button
            login_button = page.locator('button[name="login"]').first
            login_button.scroll_into_view_if_needed()
            login_button.click()
            logging.info("Login button clicked.")
            random_sleep(3, 5)

            # Verify login success
            if is_logged_in(page):
                if not session_cookie:
                    session_cookie = context.storage_state()     
                browser.close()
                logging.info("Login successful.")
                return session_cookie
            else:
                browser.close()
                return None
            

    except Exception as e:
        logging.error(f"Error during login: {e}")
        raise

def is_logged_in(page):
    """Check if the user is logged in."""
    try:
        page.wait_for_selector("//div[@aria-label='Facebook' or @aria-label='Home' or contains(@class, 'x1qhmfi1')]", timeout=10000)
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
            random_sleep(0.5, 1.5)
            cookie_buttons.first.click()
            logging.info("Cookie consent handled.")
    except Exception as e:
        logging.warning(f"No cookie banner found or already accepted: {e}")
