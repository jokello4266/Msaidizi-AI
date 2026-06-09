-- 1. Create a table for the Instagram/WhatsApp Vendors (Shop Owners)
CREATE TABLE vendors (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    business_name VARCHAR(255) NOT NULL,
    whatsapp_number VARCHAR(50) UNIQUE NOT NULL, -- Vendor's phone used to upload stock
    instagram_username VARCHAR(100) UNIQUE NOT NULL, -- Shop's IG handle
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- 2. Create the Inventory Table to hold items, prices, and photo URLs
CREATE TABLE inventory (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    vendor_id UUID REFERENCES vendors(id) ON DELETE CASCADE,
    item_name VARCHAR(255) NOT NULL,
    sizes INT[] NOT NULL, -- Stores arrays like [40, 41, 42]
    retail_price NUMERIC(10, 2) NOT NULL, -- The sticker price (e.g., 4500.00)
    floor_price NUMERIC(10, 2) NOT NULL,  -- The lowest acceptable bargain price (e.g., 4000.00)
    image_url TEXT NOT NULL, -- Link to the photo stored in Supabase Storage
    is_in_stock BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- 3. Create a table to track Customer Conversations (For AI short-term memory)
CREATE TABLE conversations (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    customer_ig_id VARCHAR(255) NOT NULL, -- Unique ID from Instagram DM
    vendor_id UUID REFERENCES vendors(id) ON DELETE CASCADE,
    chat_history JSONB DEFAULT '[]'::jsonb, -- Stores the message logs for context
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);
