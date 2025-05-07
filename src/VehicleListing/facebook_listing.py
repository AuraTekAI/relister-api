import logging
import random
import time
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError
import os
import requests
from relister.settings import IMAGES_DIR
import re

logging = logging.getLogger('facebook')
def human_like_typing(element, text):
    """Simulate human-like typing with random delays."""
    for char in text:
        element.type(char, delay=random.uniform(2, 5))  
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
            suggestion = page.locator(f"//div[@role='option' or @role='listbox']//span[contains(text(), '{value}')]").first
            suggestion.click()
        except:
            input_element.press("Enter")

    if use_tab:
        input_element.press("Tab")

    random_sleep(1, 2)  # Random delay after filling the field
    return True


def select_dropdown_option(page, field_name, option_text):
    """Selects a dropdown option with retries, visibility checks, and logging."""
    max_retries = 3
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
                    random_sleep(1, 2)
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
                    random_sleep(1, 2)
                    return True, f"{field_name} = {option_text} selected successfully"
                except Exception as e:
                    logging.debug(f"Option selector failed: {option_selector} => {e}")
                    continue

            # Fallback: Fill and Enter
            dropdown.fill(option_text)
            dropdown.press("Enter")
            logging.info(f"Filled and submitted '{option_text}' for field '{field_name}'.")
            random_sleep(1, 2)
            return True, f"{field_name} = {option_text} entered manually"

        except PlaywrightTimeoutError as te:
            logging.warning(f"Timeout on attempt {attempt} for field '{field_name}': {te}")
        except Exception as e:
            logging.error(f"Error on attempt {attempt} for field '{field_name}': {e}")

        if attempt < max_retries:
            wait_time = random.uniform(1, 3)
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
            random_sleep(0.5, 1)

            car_option = page.locator("//div[@role='option'][contains(.,'Other')]").first
            car_option.wait_for(state="visible", timeout=7000)
            car_option.scroll_into_view_if_needed()
            car_option.click()
            logging.info("Vehicle type (Other) selected successfully.")
            random_sleep(1, 2)

            return True, "Other"

        except PlaywrightTimeoutError as te:
            logging.warning(f"Timeout waiting for element on attempt {attempt}: {te}")
        except Exception as e:
            logging.error(f"Error on attempt {attempt}: {e}")

        if attempt < max_retries:
            wait_time = random.uniform(1, 3)
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
                    random_sleep(1, 2)
            except Exception as e:
                logging.warning(f"Failed to click 'Not now': {e}")
                # Try close button as fallback
                try:
                    close_button = page.locator("div[aria-label='Close'][role='button']").first
                    close_button.click(force=True)
                    logging.info("Clicked close button")
                    random_sleep(1, 2)
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
                random_sleep(2, 3)
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
                    random_sleep(2, 3)

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
                                random_sleep(2,3)
                                return True
                        except Exception as e:
                            logging.warning(f"Failed option selector {option_selector}: {e}")
                            continue

                    # As fallback: type and press Enter
                    dropdown.fill(display_make)
                    dropdown.press("Enter")
                    logging.info("↩️ Typed and entered value in dropdown fallback")
                    random_sleep(1, 2)
                    return True
            except Exception as e:
                logging.warning(f"Failed dropdown selector {selector}: {e}")
                continue
    else:
        logging.warning(f"Make '{make_value}' not recognized.")
        return False, f"Make '{make_value}' not in supported list"

    logging.error("Failed to handle 'Make' field.")
    return False, "Failed to locate or interact with 'Make' field"




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
                return False, "Timeout error navigating to Facebook Marketplace vehicle listing page"
            logging.info("Navigated to Facebook Marketplace vehicle listing page.")
            random_sleep(2, 3)  # Random delay after page load
            logging.info("Page loaded successfully.")
            logging.info(f"create market place listing for {vehicle_listing.list_id} and for user {vehicle_listing.user.email} and vehicle title is {vehicle_listing.year} {vehicle_listing.make} {vehicle_listing.model}")
            # Quick check for modal and handle if exists
            handle_login_info_modal(page)

            # Check if the "Limit reached" element is visible
            limit_reached_selector = "//span[@class='x1lliihq x6ikm8r x10wlt62 x1n2onr6']//span[contains(@class, 'x193iq5w') and text()='Limit reached']"
            if is_element_visible(page, limit_reached_selector):
                logging.info("Limit reached element is visible.")
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
                return False, result[1]
            random_sleep(4, 5)

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
                        return False, "Image download failed, file does not exist." 

                    # Upload images
                    image_input = page.locator("//input[@type='file']").first
                    image_input.set_input_files(local_image_path)
                    logging.info("Photos uploaded successfully.")
                    random_sleep(9, 12)  # Random delay after uploading images
            else:
                logging.info("No images found.")
                return False, "No images found."
            random_sleep(4, 5)
            

            result = select_dropdown_option(page, "Year", vehicle_details["Year"])
            if result[0]:
                logging.info(f"Year selected successfully: {result[1]}")
            else:
                logging.info(f"Failed to select Year: {result[1]}")
                return False, result[1]

            handle_make_field(page, vehicle_listing.make)

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
                

            # Submit form
            for button_text in ["Next", "Publish"]:
                random_sleep(10,15)
                success, message = click_button_when_enabled(page, button_text, max_attempts=3, wait_time=3)
                if not success:
                    # Optionally handle the failure
                    return False, message
                else:
                    # Add random sleep if needed
                    random_sleep(10, 15)

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
            random_sleep(0.5, 1.5)
            cookie_buttons.first.click()
            logging.info("Cookie consent handled.")
    except Exception as e:
        logging.warning(f"No cookie banner found or already accepted: {e}")


def extract_listings_with_status(text):
    """
    Extracts structured listings including title, price, date, and status.
    Returns a list of dicts: title, price, listing_date, status.
    """
    listing_pattern = r'(.*?)(AU\$\d{1,3}(?:,\d{3})*).*?Listed on (\d{2}/\d{2})(.*?)(?=(?:\d{4}|\Z))'
    matches = re.findall(listing_pattern, text, re.DOTALL)

    listings = []
    for title, price, listing_date, tail in matches:
        title_clean = title.strip().replace('\xa0', ' ')
        tail_clean = tail.lower().replace('\xa0', ' ')

        if "mark as sold" in tail_clean:
            status = "Mark as sold"
        elif "mark as available" in tail_clean:
            status = "Mark as available"
        else:
            status = None

        listings.append({
            'title': title_clean,
            'price': "".join(filter(str.isdigit, price)),
            'listing_date': listing_date,
            'status': status
        })

    return listings

def get_count_of_elements_with_text(search_for, page):
    """Get count of elements with text"""
    return len(get_elements_with_text(search_for, page))

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
            title = title_el.text_content().strip() if title_el else None

            price_el = parent.query_selector("span:has-text('AU$')")
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
        # active_el = page.query_selector("span:has-text('Active')")
        # print(active_el.text_content())
        # if active_el:
        #     filter_listings_with_date = extract_listings_with_status(active_el.text_content())

        sold_el = page.query_selector("span:has-text('Listed on')")
        # print(sold_el.text_content())
        if sold_el:
            filter_listings_with_date += extract_listings_with_status(sold_el.text_content())
        # date=page.query_selector("span:has-text('Listed on')")
        # print(date.text_content())

    except Exception as e:
        logging.error(f"Error extracting listing metadata: {e}")

    # Match and enrich
    for search in search_listings:
        for record in filter_listings_with_date:
            if (search['title'].lower() == record['title'].lower()
                and search['price'] == record['price']):
                search['date'] = record['listing_date']
                search['status'] = record['status']
                break
    print("search_listings",search_listings)
    print("filter_listings_with_date",filter_listings_with_date)
    return search_listings

def perform_search_and_delete(search_for, listing_price, listing_date, session_cookie):
    """Perform search and delete listing with retry and timeout handling"""

    def handle_post_delete_flow(page, browser):
        try:
            not_answer_button = page.locator("//*[text()=\"I'd rather not answer\"]").first
            if not_answer_button and not_answer_button.is_visible():
                not_answer_button.click()
                random_sleep(2, 3)
            else:
                logging.warning("'I'd rather not answer' button not found.")
                return 1, "'I'd rather not answer' button not found, but successfully deleted the product"

            next_button = page.locator("//*[text()='Next']").first
            if next_button and next_button.is_visible():
                next_button.click()
                random_sleep(2, 3)
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
            random_sleep(3, 5)

            if not search_for.strip():
                browser.close()
                return 0, "Search value is required"

            input_locator = "input[type='text'][placeholder='Search your listings'], input[type='text'][aria-label='Search your listings']"
            input_element = page.locator(input_locator).first

            if not input_element.is_visible():
                browser.close()
                return 0, "Search input not found"

            input_element.click()
            input_element.fill(search_for)
            page.wait_for_timeout(3000)

            formatted_date = listing_date.strftime("%d/%m")
            matches_found = get_count_of_elements_with_text(search_for, page)

            if matches_found == 0:
                if page.locator("text='We didn't find anything'").is_visible():
                    logging.info("Detected 'We didn't find anything'")
                    browser.close()
                    return 2, "didnt_find_anything_displayed"
                else:
                    browser.close()
                    return 2, "No matching listing found"

            logging.info(f"Found {matches_found} match(es) for '{search_for}'")
            elements = get_elements_with_text(search_for, page)

            for element in elements:
                try:
                    title_match = element['title'] and element['title'].lower() == search_for.lower()
                    price_match = element['price'] == "".join(filter(str.isdigit, listing_price))
                    date_match = element['date'] == formatted_date
                    status = element.get('status', '').lower()

                    if title_match and price_match and date_match:
                        if status == "mark as sold":
                            logging.info(f"Deleting listing: {element['title']} - {element['price']}")

                            price_element = element.get('price_element')
                            if price_element and price_element.is_visible():
                                price_element.click()
                                random_sleep(3, 5)

                                success, message = find_and_click_delete_button(page)
                                if not success:
                                    browser.close()
                                    return 0, message

                                delete_buttons = page.locator("span.x1lliihq.x6ikm8r.x10wlt62.x1n2onr6.xlyipyv.xuxw1ft:has-text('Delete')").all()
                                if delete_buttons:
                                    target_button = delete_buttons[2]
                                    if target_button.is_visible():
                                        target_button.click()
                                        random_sleep(3, 4)
                                        return handle_post_delete_flow(page, browser)
                                logging.error("Delete button not found or not visible.")
                                browser.close()
                                return 0, "Delete button not found"
                            else:
                                browser.close()
                                return 0, "Price element not found or not visible"

                        elif status == "mark as available":
                            logging.info("Listing is already marked as available.")
                            browser.close()
                            return 2, "This listing is already sold"
                except Exception as e:
                    logging.error(f"Error evaluating listing match: {e}")
                    continue

            browser.close()
            return 0, "No matching listing found"

    except Exception as e:
        logging.error(f"Unhandled error in perform_search_and_delete: {e}")
        if 'browser' in locals():
            browser.close()
        return 0, str(e)



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

def random_delay(min_time=1, max_time=3):
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
            random_delay(2, 5)

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
            print(parts)
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
            random_delay(1, 2)

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