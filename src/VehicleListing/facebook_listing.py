import logging
import random
import time
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError
import os
import requests
from .models import VehicleListing
# Configure logging
logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(levelname)s - %(message)s')

images_folder = os.path.join(os.path.dirname(__file__), '..', 'static', 'images')

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

def create_marketplace_listing(vehicle_listing,session_cookie):
    """Create a new listing on Facebook Marketplace with human-like interactions."""
    try:    
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(storage_state=session_cookie)
            page = context.new_page()

            # Navigate to the vehicle listing page
            page.goto("https://www.facebook.com/marketplace/create/vehicle", timeout=60000)
            logging.info("Navigated to Facebook Marketplace vehicle listing page.")
            random_sleep(2, 3)  # Random delay after page load

            # Vehicle details
            vehicle_details = {
                "Year": vehicle_listing.year,
                "Make": vehicle_listing.make,
                "Model": vehicle_listing.model,
                "Price": str(vehicle_listing.price),
                "Location": vehicle_listing.location,
                "Mileage": str(vehicle_listing.mileage),
                "Description": vehicle_listing.description
            }

            # Select vehicle type
            select_vehicle_type(page)

            # Download the image and save it locally    
            image_name = os.path.basename(vehicle_listing.images)
            image_extension = os.path.splitext(image_name)[1] 
            new_image_name = f"{vehicle_listing.list_id}_image{image_extension}"  
            local_image_path = os.path.join(images_folder, new_image_name)

            try:
                # Download the image
                image_response = requests.get(vehicle_listing.images)
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
            # select_dropdown_option(page, "Body style", "Sedan")
            # select_dropdown_option(page, "Fuel type", vehicle_listing.fuel_type)
            # select_dropdown_option(page, "Vehicle condition", "Excellent")

            # Select dropdowns
            if vehicle_listing.variant in ["Coupe", "Sedan", "SUV", "Truck","Hatchback","Convertible","Minivan","Small Car", "Wagon"]:
                select_dropdown_option(page, "Body style", vehicle_listing.variant)
            else:
                select_dropdown_option(page, "Body style", "Other")
            
            if vehicle_listing.fuel_type in ["Petrol", "Diesel","Gasoline","Flex","Plug-in Hybrid","Electric", "Hybrid"]:
                select_dropdown_option(page, "Fuel type", vehicle_listing.fuel_type)
            elif vehicle_listing.fuel_type == "Petrol - Premium Unleaded":
                select_dropdown_option(page, "Fuel type", "Petrol")
            elif vehicle_listing.fuel_type == "Petrol - Regular Unleaded":
                select_dropdown_option(page, "Fuel type", "Petrol")
            else:
                select_dropdown_option(page, "Fuel type", "Other")
            
            
            select_dropdown_option(page, "Vehicle condition", "Excellent")
            
            if vehicle_listing.transmission in ["Automatic Transmission", "Automatic"]:
                select_dropdown_option(page, "Transmission", "Automatic transmission")
            elif vehicle_listing.transmission in ["Manual Transmission", "Manual"]:
                select_dropdown_option(page, "Transmission", "Manual transmission")

            else:
                select_dropdown_option(page, "Transmission", "Automatic transmission")

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
                    return False, "Failed to click button"

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
            browser = p.chromium.launch(headless=True)
            context = browser.new_context()
            page = context.new_page()
            # Navigate to Facebook login page
            page.goto("https://www.facebook.com/login", timeout=30000)
            logging.info("Navigated to Facebook login page.")
            random_sleep(2, 3)  # Random delay after page load

            # Handle cookie consent
            handle_cookie_consent(page)

            # Fill email field
            email_field = page.locator('input[name="email"]').first
            email_field.scroll_into_view_if_needed()
            human_like_typing(email_field, email)
            logging.info("Email filled successfully.")
            random_sleep(2,5)

            # Fill password field
            password_field = page.locator('input[name="pass"]').first
            password_field.scroll_into_view_if_needed()
            human_like_typing(password_field, password)
            logging.info("Password filled successfully.")
            random_sleep(2,5)

            # Click login button
            login_button = page.locator('button[name="login"]').first
            login_button.scroll_into_view_if_needed()
            login_button.click()
            logging.info("Login button clicked.")
            random_sleep(2, 5)

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
        


def perform_search_and_delete(search_for,session_cookie):
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(storage_state=session_cookie)
            page = context.new_page()
            page.goto("https://www.facebook.com/marketplace/you/selling")
            page.wait_for_timeout(5000)
            logging.info("Navigated to Facebook Marketplace vehicle listing page.")
            random_sleep(3, 5)
            if not search_for.strip():
                return False, "Search value is required"
            

            input_locator = "input[type='text'][placeholder='Search your listings'], input[type='text'][aria-label='Search your listings']"
            input_element = page.locator(input_locator).first

            if not input_element.is_visible():
                return False, "Search input not found"

            logging.info(f"Your Listings: Search: {search_for}")
            input_element.click()
            input_element.fill(search_for)
            page.wait_for_timeout(5000)  # Wait for 5 seconds

            matches_found = get_count_of_elements_with_text(search_for,page)
            if matches_found > 0:
                logging.info(f"Success (Attempt 1): Found ({matches_found}) matches for ({search_for})")
                # Open the "More options" menu
                more_options = page.locator(
                "//div[contains(@class, 'x1n2onr6')]//div[contains(@class, 'x1ja2u2z')]//div[@aria-label and contains(@aria-label, 'More options')]"
            ).first
                more_options.click()
                page.wait_for_timeout(2000)

                # Select "Delete listing" from the menu
                delete_option = page.wait_for_selector(
                    "//div[@role='menuitem']//span[contains(text(), 'Delete listing')]//ancestor::div[@role='menuitem']",
                    state="visible",
                )
                delete_option.click()
                page.wait_for_timeout(2000)

                # Confirm deletion
                logging.info("Confirming deletion...")
                confirm_delete = page.wait_for_selector(
                    "//div[@aria-label='Delete' and contains(@class, 'x1i10hfl') and contains(@class, 'xjbqb8w') and @role='button' and @tabindex='0']",
                    state="visible",
                )
                confirm_delete.click()
                page.wait_for_timeout(3000)

                # Handle post-deletion actions
                logging.info("Clicking 'I'd rather not answer'...")
                not_answer_button = page.locator("//*[text()=\"I'd rather not answer\"]").first
                if not_answer_button:
                    not_answer_button.click()
                    page.wait_for_timeout(2000)
                else:
                    logging.warning("'I'd rather not answer' button not found.")
                    return True, "I'd rather not answer' button not found.but successfully delte the product"

                logging.info("Clicking 'Next'...")
                next_button = page.locator("//*[text()='Next']").first
                if next_button and next_button.is_visible():
                    next_button.click()
                    page.wait_for_timeout(2000)
                    logging.info("Process completed successfully.")
                    return True, "Successfully deleted the  listing"
                else:
                    logging.warning("'Next' button not found.")
                    return True, "'Next' button not found.but successfully delte the product"

            page.wait_for_timeout(5000)  # Wait for another 5 seconds
            matches_found = get_count_of_elements_with_text(search_for,page)
            if matches_found > 0:
                logging.info(f"Success (Attempt 2): Found ({matches_found}) matches for ({search_for})")
                # Open the "More options" menu
                more_options = page.locator(
                    "//div[contains(@class, 'x1n2onr6')]//div[contains(@class, 'x1ja2u2z')]//div[@aria-label and contains(@aria-label, 'More options')]"
                ).first
                more_options.click()
                page.wait_for_timeout(2000)

                # Select "Delete listing" from the menu
                delete_option = page.wait_for_selector(
                    "//div[@role='menuitem']//span[contains(text(), 'Delete listing')]//ancestor::div[@role='menuitem']",
                    state="visible",
                )
                delete_option.click()
                page.wait_for_timeout(2000)

                # Confirm deletion
                logging.info("Confirming deletion...")
                confirm_delete = page.wait_for_selector(
                    "//div[@aria-label='Delete' and contains(@class, 'x1i10hfl') and contains(@class, 'xjbqb8w') and @role='button' and @tabindex='0']",
                    state="visible",
                )
                confirm_delete.click()
                page.wait_for_timeout(3000)

                # Handle post-deletion actions
                logging.info("Clicking 'I'd rather not answer'...")
                not_answer_button = page.locator("//*[text()=\"I'd rather not answer\"]").first
                if not_answer_button:
                    not_answer_button.click()
                    page.wait_for_timeout(2000)
                else:
                    logging.warning("'I'd rather not answer' button not found.")
                    return True, "I'd rather not answer' button not found. but successfully delte the product"

                logging.info("Clicking 'Next'...")
                next_button = page.locator("//*[text()='Next']").first
                if next_button and next_button.is_visible():
                    next_button.click()
                    page.wait_for_timeout(2000)
                    logging.info("Process completed successfully.")
                    return True, "Successfully deleted the  listing"
                else:
                    logging.warning("'Next' button not found.")
                    return True, "'Next' button not found.but successfully delte the product"
            

            didnt_find_locator = "text='We didn't find anything'"
            if page.locator(didnt_find_locator).is_visible():
                logging.info("Success (Attempt 3): Detected 'We didn't find anything'")
                return  True, "didnt_find_anything_displayed"
    except Exception as e:
        logging.error(f"Error in perform_search_and_delete: {e}")
        return False, str(e)

def get_count_of_elements_with_text( search_for,page):
    return len(get_elements_with_text(search_for,page))

def get_elements_with_text(search_for,page):
    locator_case_sensitive = f"text={search_for}"
    locator_case_insensitive = f"text=/.*{search_for}.*/i"
    elements = page.locator(locator_case_sensitive).all()
    return elements if elements else page.locator(locator_case_insensitive).all()




def get_facebook_profile_listings(profile_url, session_cookie):
    """Get all listings from any Facebook Marketplace profile URL."""
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(storage_state=session_cookie)
            page = context.new_page()
            
            # Set shorter timeout for navigation
            page.set_default_timeout(20000)
            
            # Navigate to profile URL
            page.goto(profile_url)
            page.wait_for_timeout(1000)

            # Extract profile ID from URL
            profile_id = profile_url.split('/')[-1] if profile_url.endswith('/') else profile_url.split('/')[-1]
            
            # More specific selectors for profile listings
            listing_selectors = [
                f'div[style*="max-width: 175px"] a[href*="/marketplace/item/"]',
                # f'a[href*="/marketplace/item/"]'
            ]
            
            # First hover over the listings container
            listings_container = page.locator('div[style*="max-width: 175px"]').first
            listings_container.hover()
            
            previous_count = 0
            same_count_iterations = 0
            max_same_count = 4  # Increased to 4 attempts
            start_time = time.time()
            max_time = 75  # Increased to 75 seconds for more thorough scrolling
            
            while same_count_iterations < max_same_count and (time.time() - start_time) < max_time:
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
                    page.wait_for_timeout(400)
                    
                    # Try to scroll the last element into view
                    try:
                        for selector in listing_selectors:
                            elements = page.query_selector_all(selector)
                            if elements:
                                elements[-1].scroll_into_view_if_needed()
                                break
                    except Exception:
                        continue
                
                # Get current count using all selectors
                max_count = 0
                for selector in listing_selectors:
                    try:
                        count = len(page.query_selector_all(selector))
                        max_count = max(max_count, count)
                    except:
                        continue
                
                logging.info(f"Current item count: {max_count}")
                
                if max_count > previous_count:
                    previous_count = max_count
                    same_count_iterations = 0
                    logging.info(f"Found new items ({max_count}), continuing to scroll...")
                else:
                    same_count_iterations += 1
                    logging.info(f"No new items found. Attempt {same_count_iterations} of {max_same_count}")
            
            # Final thorough scroll
            for _ in range(3):
                page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                page.wait_for_timeout(1000)
                page.keyboard.press("End")
                page.wait_for_timeout(1000)
            
            # Use sets to track seen listings
            seen_ids = set()
            seen_titles = set()
            listings = []
            
            for selector in listing_selectors:
                elements = page.query_selector_all(selector)
                for element in elements:
                    try:
                        href = element.get_attribute('href')
                        if not href or '/marketplace/item/' not in href:
                            continue
                            
                        listing_id = href.split('/item/')[1].split('/')[0]
                        
                        # Extract title first for duplicate checking
                        title_element = element.query_selector('span[style*="-webkit-line-clamp: 2"]')
                        if not title_element:
                            continue
                            
                        title = title_element.text_content()
                        
                        # Skip if we've seen this ID or title
                        if listing_id in seen_ids or title in seen_titles:
                            continue
                            
                        # Extract other details
                        price_element = element.query_selector('span:has-text("$")')
                        location_element = element.query_selector('span[class*="xlyipyv"]')
                        
                        price = price_element.text_content() if price_element else "Price not available"
                        location = location_element.text_content() if location_element else "Location not available"
                        
                        
                        listing = {
                            'id': listing_id,
                            'title': title,
                            'price': price,
                            'location': location,
                            'url': f"https://www.facebook.com/marketplace/item/{listing_id}/",
                        }
                        
                        # Add to tracking sets and listings list
                        seen_ids.add(listing_id)
                        seen_titles.add(title)
                        listings.append(listing)
                        logging.info(f"Successfully extracted listing: {title}")
                        
                    except Exception as e:
                        logging.error(f"Error extracting listing details: {str(e)}")
                        continue
            
            browser.close()
            logging.info(f"Successfully extracted {len(listings)} unique listings")
            return True, listings
            
    except Exception as e:
        logging.error(f"Error in get_profile_listings: {e}")
        if 'browser' in locals():
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
                "make": "",
                "model": "",
                "price": None,
                "mileage": None,
                "description": "",
                "images": [],
                "location": ""
            }

            # Extract each part of the listing
            extract_price(page, listing)
            random_delay()
            extract_mileage(page, listing)
            random_delay()
            extract_year_make_model(page, listing)
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
            price_str = "".join(filter(str.isdigit, text))
            if price_str:
                listing["price"] = int(price_str)
    except Exception as e:
        logging.error(f"Error extracting price: {e}")

def extract_mileage(page, listing):
    """
    Extracts the mileage from the listing.
    """
    try:
        element = page.query_selector("//span[contains(text(), 'Driven')]")
        if element:
            text = element.inner_text()
            logging.info(f"Mileage text: {text}")
            mileage_str = "".join(filter(str.isdigit, text))
            if mileage_str:
                listing["mileage"] = int(mileage_str)
    except Exception as e:
        logging.error(f"Error extracting mileage: {e}")

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
                            return  # Exit after successful extraction
                    else:
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
            return element.inner_text()
    except Exception as e:
        logging.error(f"Error extracting location: {e}")
    return ""



def save_facebook_listing(listing_details,current_listing,user,seller_id):
    try:
        VehicleListing.objects.create(
            user=user,
            list_id=current_listing["id"],
            year=listing_details.get("year"),
            body_type="Other",
            fuel_type="Other",
            color="Other",
            variant="Other",
            make=listing_details.get("make"),
            # mileage=current_listing["mileage"],
            mileage=0,
            model=listing_details.get("model"),
            price=str(listing_details.get("price")),
            transmission=None,
            description=listing_details.get("description"),
        images=listing_details["images"][0],
            url=current_listing["url"],
            location=listing_details.get("location"),
            status="pending",
            seller_profile_id=seller_id
            )
        # response_vehicle_listing_data = {   
        #     "id": vehicle_listing.id,
        #     "title": vehicle_listing.title,
        #     "price": vehicle_listing.price,
        #     "location": vehicle_listing.location,
        #     "url": vehicle_listing.url,
        #     "status": vehicle_listing.status,
        #     "seller_profile_id": vehicle_listing.seller_profile_id,
        #     "make": vehicle_listing.make,
        #     "mileage": vehicle_listing.mileage,
        #     "model": vehicle_listing.model,
        #     "price": vehicle_listing.price,
        #     "transmission": vehicle_listing.transmission,
        #     "description": vehicle_listing.description,
        #     "images": vehicle_listing.images,
        #     "url": vehicle_listing.url,
        #     "location": vehicle_listing.location,
        #     "status": vehicle_listing.status,
        #     "seller_profile_id": vehicle_listing.seller_profile_id

        # }
        # print(response_vehicle_listing_data)
        return True,"Listing saved successfully"
    except Exception as e:
        return False,str(e)